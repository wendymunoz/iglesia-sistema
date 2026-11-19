import bcrypt
from datetime import datetime
from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from database import Persona, Usuario


def hashear_pin(pin: str) -> str:
    return bcrypt.hashpw(pin.encode(), bcrypt.gensalt()).decode()


def verificar_pin(pin: str, pin_hash: str) -> bool:
    try:
        return bcrypt.checkpw(pin.encode(), pin_hash.encode())
    except Exception:
        return False


def pin_inicial(dni: str) -> str:
    """Primeros 4 dígitos del DNI."""
    digits = "".join(c for c in dni if c.isdigit())
    return digits[:4] if len(digits) >= 4 else digits.ljust(4, "0")


def get_session_user(request: Request) -> dict | None:
    return request.session.get("usuario")


def login_session(request: Request, persona: Persona, usuario: Usuario, db: Session):
    usuario.ultimo_acceso = datetime.utcnow()
    db.commit()
    request.session["usuario"] = {
        "persona_id": persona.id,
        "dni": persona.dni,
        "nombre": persona.nombre_completo,
        "roles": persona.lista_roles(),
        "debe_cambiar_pin": usuario.debe_cambiar_pin,
    }


def logout_session(request: Request):
    request.session.clear()


def redirect_login():
    return RedirectResponse(url="/login", status_code=303)


def check_auth(request: Request):
    """Devuelve el usuario de sesión o None."""
    return get_session_user(request)


def require_admin(request: Request):
    u = get_session_user(request)
    if not u:
        return None, redirect_login()
    if "admin" not in u["roles"]:
        return None, RedirectResponse(url="/dashboard", status_code=303)
    return u, None


def require_roles(request: Request, *roles):
    """Permite acceso si tiene cualquiera de los roles dados (o es admin)."""
    u = get_session_user(request)
    if not u:
        return None, redirect_login()
    allowed = set(roles) | {"admin"}
    if not any(r in allowed for r in u["roles"]):
        return None, RedirectResponse(url="/dashboard", status_code=303)
    return u, None
