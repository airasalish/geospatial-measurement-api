"""/api/rasters endpoints: DEM upload, volumetrics, profiles, altitude."""

import json
import tempfile
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path as FsPath
from typing import Annotated

import rasterio
from fastapi import APIRouter, File, Form, Path, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse
from shapely.geometry import shape
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app import metrics
from app.api.files import PageDep, SessionDep, SettingsDep, StorageDep, _errors, owner_and_project
from app.auth import UserDep
from app.errors import FileTooLarge
from app.models import Feature, Raster, User
from app.schemas import (
    AltitudeOut,
    BaseSpec,
    BatchVolumeItem,
    BatchVolumeOut,
    BatchVolumeRequest,
    Page,
    ProfileOut,
    ProfileRequest,
    RasterOut,
    VolumeOut,
    VolumeRequest,
)
from app.services import queries, rasters
from app.storage import S3Storage, Storage
from app.terrain.analysis import TerrainError, compute_profile, compute_volume, sample_altitude
from app.terrain.tiles import Style, render_tile, tile_zoom_range

router = APIRouter(prefix="/api/rasters", tags=["terrain"])
UPLOAD_CHUNK = 1024 * 1024


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=RasterOut,
    responses=_errors,
    summary="Upload a DEM/DSM GeoTIFF (converted to a Cloud-Optimised GeoTIFF)",
)
async def upload_raster(
    request: Request,
    response: Response,
    settings: SettingsDep,
    storage: StorageDep,
    user: UserDep,
    file: Annotated[UploadFile, File(description="Single-band elevation GeoTIFF (.tif/.tiff)")],
    project_id: Annotated[str | None, Form(description="Project to file this DEM under")] = None,
) -> RasterOut:
    filename = file.filename or ""
    rasters.check_raster_extension(filename)
    owner, project = owner_and_project(request, user, project_id)
    with tempfile.TemporaryDirectory() as tmp:
        path = FsPath(tmp) / "upload.tif"
        size = 0
        with path.open("wb") as fh:  # stream to disk: DEMs are too big for memory
            while chunk := await file.read(UPLOAD_CHUNK):
                size += len(chunk)
                if size > settings.max_raster_upload_bytes:
                    raise FileTooLarge(
                        f"DEM exceeds the {settings.max_raster_upload_bytes // (1024 * 1024)} MB "
                        "upload limit."
                    )
                fh.write(chunk)

        def _process() -> RasterOut:
            with request.app.state.db.session_factory() as session:
                return RasterOut.model_validate(
                    rasters.ingest_raster(session, settings, storage, filename, path, owner, project)
                )

        def _enqueue() -> RasterOut:
            from app import worker

            with request.app.state.db.session_factory() as session:
                record = rasters.create_raster_record(session, filename, path, owner, project)
                key = f"uploads/rasters/{record.id}/{record.filename}"
                storage.put(path, key)
                worker.process_raster.delay(record.id, key)
                session.refresh(record)
                return RasterOut.model_validate(record)

        if settings.task_mode == "celery":
            out = await run_in_threadpool(_enqueue)
            response.status_code = status.HTTP_202_ACCEPTED
        else:
            out = await run_in_threadpool(_process)
    response.headers["Location"] = str(request.url_for("get_raster", raster_id=out.id))
    return out


@router.get("/", response_model=Page[RasterOut], summary="List DEMs")
def list_rasters(
    session: SessionDep,
    user: UserDep,
    page: PageDep,
    project_id: Annotated[str | None, Query(description="Only DEMs in this project")] = None,
) -> Page[RasterOut]:
    limit, offset = page
    stmt = select(Raster)
    if user is not None:
        stmt = stmt.where(Raster.owner_id == user.id)
    if project_id is not None:
        stmt = stmt.where(Raster.project_id == project_id)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(stmt.order_by(Raster.created_at.desc()).limit(limit).offset(offset)).all()
    return Page[RasterOut](
        total=total, limit=limit, offset=offset, items=[RasterOut.model_validate(r) for r in rows]
    )


@router.get("/{raster_id}/", response_model=RasterOut, responses=_errors, summary="DEM information")
def get_raster(raster_id: str, session: SessionDep, user: UserDep) -> RasterOut:
    return RasterOut.model_validate(rasters.get_raster(session, raster_id, ready=False, user=user))


@router.delete("/{raster_id}/", status_code=204, responses=_errors, summary="Delete a DEM")
def delete_raster(raster_id: str, session: SessionDep, user: UserDep, storage: StorageDep) -> Response:
    rasters.delete_raster(session, storage, rasters.get_raster(session, raster_id, ready=False, user=user))
    return Response(status_code=204)


@router.get(
    "/{raster_id}/download/", responses=_errors, summary="Download the DEM as a Cloud-Optimised GeoTIFF"
)
def download_raster(raster_id: str, session: SessionDep, user: UserDep, storage: StorageDep) -> Response:
    record = rasters.get_raster(session, raster_id, user=user)
    assert record.storage_key
    stem = FsPath(record.filename).stem
    if storage.local_path(record.storage_key) is None and isinstance(storage, S3Storage):
        # Object storage: hand the client a short-lived presigned URL instead of proxying bytes.
        return RedirectResponse(storage.presigned_url(record.storage_key, f"{stem}.cog.tif"), status_code=307)
    return FileResponse(
        storage.gdal_path(record.storage_key), media_type="image/tiff", filename=f"{stem}.cog.tif"
    )


def _volume(
    session: Session,
    user: User | None,
    storage: Storage,
    record: Raster,
    ds: rasterio.io.DatasetReader,
    geom: object,
    base: BaseSpec,
    slope_threshold: float,
    stack: ExitStack,
) -> VolumeOut:
    other = None
    if base.method == "surface":
        if not base.raster_id:
            raise TerrainError("Base method 'surface' needs 'raster_id'.")
        other_rec = rasters.get_raster(session, base.raster_id, user=user)
        other = stack.enter_context(rasters.open_raster(storage, other_rec))
    result = compute_volume(
        ds,
        geom,
        method=base.method,
        custom_elevation=base.elevation,
        other=other,
        slope_threshold_deg=slope_threshold,
    )
    return VolumeOut(raster_id=record.id, **asdict(result))


@router.post(
    "/{raster_id}/volume/",
    response_model=VolumeOut,
    responses=_errors,
    summary="Cut/fill volume, 3D surface area and slope inside a polygon",
)
def volume(
    raster_id: str, body: VolumeRequest, session: SessionDep, user: UserDep, storage: StorageDep
) -> VolumeOut:
    record = rasters.get_raster(session, raster_id, user=user)
    geom = rasters.resolve_geometry(session, body.geometry, body.feature, user)
    with ExitStack() as stack:
        ds = stack.enter_context(rasters.open_raster(storage, record))
        return _volume(session, user, storage, record, ds, geom, body.base, body.slope_threshold_deg, stack)


@router.post(
    "/{raster_id}/volumes/",
    response_model=BatchVolumeOut,
    responses=_errors,
    summary="Volumes for every polygon of an uploaded file (e.g. a stockpile inventory)",
)
def batch_volumes(
    raster_id: str, body: BatchVolumeRequest, session: SessionDep, user: UserDep, storage: StorageDep
) -> BatchVolumeOut:
    record = rasters.get_raster(session, raster_id, user=user)
    queries.get_file(session, body.file_id, user)
    features = session.scalars(
        select(Feature).where(Feature.file_id == body.file_id).order_by(Feature.index)
    ).all()
    items: list[BatchVolumeItem] = []
    totals = {"cut_m3": 0.0, "fill_m3": 0.0, "net_m3": 0.0, "area_2d_m2": 0.0, "surface_area_3d_m2": 0.0}
    with ExitStack() as stack:
        ds = stack.enter_context(rasters.open_raster(storage, record))
        for f in features:
            name = (f.properties or {}).get("name") or queries.display_name(f.properties)
            if f.geometry_type not in ("Polygon", "MultiPolygon") or not f.geometry_wgs84:
                items.append(
                    BatchVolumeItem(index=f.index, name=name, status="SKIPPED", message="Not a polygon.")
                )
                continue
            try:
                out = _volume(
                    session,
                    user, storage, record, ds, shape(json.loads(f.geometry_wgs84)),
                    body.base, body.slope_threshold_deg, stack,
                )  # fmt: skip
            except TerrainError as exc:
                items.append(BatchVolumeItem(index=f.index, name=name, status="FAILED", message=exc.message))
                continue
            for key in totals:
                totals[key] += getattr(out, key)
            items.append(BatchVolumeItem(index=f.index, name=name, status="OK", result=out))
    return BatchVolumeOut(
        raster_id=record.id,
        file_id=body.file_id,
        base_method=body.base.method,
        totals={k: round(v, 3) for k, v in totals.items()},
        measured=sum(i.status == "OK" for i in items),
        skipped=sum(i.status == "SKIPPED" for i in items),
        failed=sum(i.status == "FAILED" for i in items),
        items=items,
    )


@router.post(
    "/{raster_id}/profile/",
    response_model=ProfileOut,
    responses=_errors,
    summary="Elevation profile along a line (haul-road grades, cross-sections)",
)
def profile(
    raster_id: str, body: ProfileRequest, session: SessionDep, user: UserDep, storage: StorageDep
) -> ProfileOut:
    record = rasters.get_raster(session, raster_id, user=user)
    geom = rasters.resolve_geometry(session, body.geometry, body.feature, user)
    with rasters.open_raster(storage, record) as ds:
        result = compute_profile(ds, geom, max_samples=body.max_samples)
    return ProfileOut(raster_id=record.id, **asdict(result))


@router.get(
    "/{raster_id}/altitude/", response_model=AltitudeOut, responses=_errors, summary="Elevation at a point"
)
def altitude(
    raster_id: str,
    session: SessionDep,
    user: UserDep,
    storage: StorageDep,
    lon: Annotated[float, Query(ge=-180, le=180)],
    lat: Annotated[float, Query(ge=-90, le=90)],
) -> AltitudeOut:
    record = rasters.get_raster(session, raster_id, user=user)
    with rasters.open_raster(storage, record) as ds:
        z = sample_altitude(ds, lon, lat)
    return AltitudeOut(raster_id=record.id, lon=lon, lat=lat, elevation_m=z)


@router.get(
    "/{raster_id}/tiles/{z}/{x}/{y}.png",
    responses={**_errors, 200: {"content": {"image/png": {}}}, 204: {"description": "Tile outside the DEM"}},
    summary="XYZ map tile rendered from the DEM",
)
def tile(
    raster_id: str,
    z: Annotated[int, Path(ge=0, le=24)],
    x: Annotated[int, Path(ge=0)],
    y: Annotated[int, Path(ge=0)],
    session: SessionDep,
    user: UserDep,
    storage: StorageDep,
    style: Annotated[Style, Query(description="hillshade | elevation | slope")] = "elevation",
) -> Response:
    record = rasters.get_raster(session, raster_id, user=user)
    assert record.storage_key
    with metrics.ANALYSIS.labels("tile").time():
        png = render_tile(
            storage.gdal_path(record.storage_key),
            z, x, y, style,
            (record.z_min or 0.0, record.z_max or 1.0),
            storage.rasterio_env(),
        )  # fmt: skip
    if png is None:
        return Response(status_code=204)
    # A raster never changes after upload, so tiles can be cached hard.
    return Response(
        png, media_type="image/png", headers={"Cache-Control": "public, max-age=86400, immutable"}
    )


@router.get("/{raster_id}/tilejson.json", responses=_errors, summary="TileJSON for map clients")
def tilejson(
    raster_id: str,
    request: Request,
    session: SessionDep,
    user: UserDep,
    storage: StorageDep,
    style: Annotated[Style, Query()] = "elevation",
) -> dict[str, object]:
    record = rasters.get_raster(session, raster_id, user=user)
    assert record.storage_key
    minzoom, maxzoom = tile_zoom_range(storage.gdal_path(record.storage_key), storage.rasterio_env())
    url = str(request.url_for("tile", raster_id=raster_id, z=0, x=0, y=0)).replace(
        "/0/0/0.png", "/{z}/{x}/{y}.png"
    )
    return {
        "tilejson": "3.0.0",
        "name": record.filename,
        "tiles": [f"{url}?style={style}"],
        "bounds": record.bbox,
        "minzoom": minzoom,
        "maxzoom": maxzoom + 2,  # allow over-zoom for close inspection
        "elevation_range": [record.z_min, record.z_max],
    }
