"""/api/auth and /api/projects endpoints."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.files import SessionDep, SettingsDep, _errors
from app.auth import Conflict, RequiredUserDep, Unauthorized, create_token, hash_password, verify_password
from app.errors import NotFound
from app.models import Project, Raster, UploadedFile, User

router = APIRouter(tags=["auth"])

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class Credentials(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=255)
    password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105  (OAuth2 token type, not a secret)
    expires_in: int


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ProjectOut(BaseModel):
    id: str
    name: str
    created_at: datetime
    files: int
    rasters: int


@router.get("/api/auth/config", summary="Whether this deployment requires sign-in")
def auth_config(settings: SettingsDep) -> dict[str, bool]:
    return {"auth_required": settings.auth_required}


@router.post("/api/auth/register", status_code=201, response_model=UserOut, responses=_errors)
def register(body: Credentials, session: SessionDep) -> UserOut:
    user = User(email=body.email.strip().lower(), password_hash=hash_password(body.password))
    session.add(user)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise Conflict("An account with this email already exists.") from exc
    return UserOut.model_validate(user)


@router.post(
    "/api/auth/token", response_model=Token, responses=_errors, summary="Sign in (OAuth2 password flow)"
)
def token(
    form: Annotated[OAuth2PasswordRequestForm, Depends()], session: SessionDep, settings: SettingsDep
) -> Token:
    user = session.scalar(select(User).where(User.email == form.username.strip().lower()))
    # Same message either way: don't reveal which emails have accounts.
    if user is None or not verify_password(form.password, user.password_hash):
        raise Unauthorized("Wrong email or password.")
    return Token(access_token=create_token(settings, user), expires_in=settings.jwt_ttl_minutes * 60)


@router.get("/api/auth/me", response_model=UserOut, responses=_errors)
def me(user: RequiredUserDep) -> UserOut:
    return UserOut.model_validate(user)


def _project_out(session: SessionDep, p: Project) -> ProjectOut:
    files = session.scalar(select(func.count()).where(UploadedFile.project_id == p.id)) or 0
    rasters = session.scalar(select(func.count()).where(Raster.project_id == p.id)) or 0
    return ProjectOut(id=p.id, name=p.name, created_at=p.created_at, files=files, rasters=rasters)


@router.get("/api/projects/", response_model=list[ProjectOut], tags=["projects"])
def list_projects(session: SessionDep, user: RequiredUserDep) -> list[ProjectOut]:
    rows = session.scalars(select(Project).where(Project.owner_id == user.id).order_by(Project.name)).all()
    return [_project_out(session, p) for p in rows]


@router.post(
    "/api/projects/", status_code=201, response_model=ProjectOut, responses=_errors, tags=["projects"]
)
def create_project(body: ProjectIn, session: SessionDep, user: RequiredUserDep) -> ProjectOut:
    project = Project(owner_id=user.id, name=body.name.strip())
    session.add(project)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise Conflict(f"You already have a project called {body.name!r}.") from exc
    return _project_out(session, project)


@router.delete("/api/projects/{project_id}/", status_code=204, responses=_errors, tags=["projects"])
def delete_project(project_id: str, session: SessionDep, user: RequiredUserDep) -> Response:
    project = session.get(Project, project_id)
    if project is None or project.owner_id != user.id:
        raise NotFound(f"Project {project_id!r} not found.")
    session.delete(project)  # files and DEMs stay; their project_id becomes NULL
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
