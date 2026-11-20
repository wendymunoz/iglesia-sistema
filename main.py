import os
import io
import json
import httpx
import openpyxl
from datetime import datetime, date, timedelta
from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import RedirectResponse, JSONResponse, StreamingResponse, HTMLResponse
from fastapi.exceptions import HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv

from database import (
    get_db, init_db,
    Persona, PersonaRol, Usuario, ProcesoEspiritual,
    Salon, Matricula, ConceptoPago, Abono, HistorialSalon, Nota,
    Celula, ReporteCelula, AsistenteCelula,
    ReunionLideres, AsistenciaLider,
    GrupoEncuentro, InscripcionEncuentro, PagoEncuentro,
    AsistenciaCulto, Evento,
    AreaServicio, ItemPlantillaArea, AsignacionCulto, ChecklistItemCulto,
    PendienteMatricula,
    Culto, Ofrenda, SiembraCocina,
    LiderG12,
    ROLES, ETAPAS, ETAPAS_DISPLAY, CONCEPTOS_NIVEL,
    saldo_matricula, calcular_nota_final,
)
from auth import (
    hashear_pin, verificar_pin, pin_inicial,
    get_session_user, login_session, logout_session,
    redirect_login, check_auth, require_admin, require_roles,
)

load_dotenv()

app = FastAPI(title="Sistema Iglesia")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SECRET_KEY", "cambiar-en-produccion"),
    max_age=86400,
)

if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    u = get_session_user(request)
    if not u:
        return RedirectResponse(url="/login", status_code=303)
    return HTMLResponse(content=f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"/><title>Página no encontrada</title>
<script src="https://cdn.tailwindcss.com"></script></head>
<body class="min-h-screen bg-amber-50 flex items-center justify-center">
  <div class="text-center p-10">
    <div style="font-size:64px">🔍</div>
    <h1 style="font-size:28px;font-weight:800;color:#1A160E;margin:16px 0 8px">Página no encontrada</h1>
    <p style="color:#8B7A5A;font-size:14px;margin-bottom:28px">Esta sección no existe o no tienes acceso.</p>
    <a href="/dashboard" style="background:linear-gradient(135deg,#D4A820,#A07818);color:#fff;font-weight:700;border-radius:10px;padding:12px 24px;text-decoration:none;font-size:14px">← Volver al dashboard</a>
  </div>
</body></html>""", status_code=404)


_db_ready = False

@app.on_event("startup")
def startup():
    global _db_ready
    import time
    for attempt in range(5):
        try:
            init_db()
            _db_ready = True
            print("[startup] DB OK")
            return
        except Exception as e:
            print(f"[startup] DB attempt {attempt+1}/5 failed: {e}")
            if attempt < 4:
                time.sleep(3)
    print("[startup] DB init could not complete — will retry on first request")


@app.middleware("http")
async def ensure_db(request, call_next):
    global _db_ready
    if not _db_ready:
        try:
            init_db()
            _db_ready = True
        except Exception as e:
            print(f"[middleware] DB init retry failed: {e}")
    return await call_next(request)


@app.get("/setup-inicial")
async def setup_inicial(request: Request, db: Session = Depends(get_db)):
    """Endpoint temporal para crear el primer admin. Solo funciona si no hay admins."""
    from database import PersonaRol, ProcesoEspiritual
    ya_hay_admin = db.query(PersonaRol).filter(PersonaRol.rol == "admin").first()
    if ya_hay_admin:
        return JSONResponse({"error": "Ya existe un administrador. Endpoint deshabilitado."}, status_code=403)
    # Crear admin por defecto
    dni = "00000001"
    p = Persona(dni=dni, nombre="Administrador", apellido="Sistema")
    db.add(p); db.flush()
    db.add(PersonaRol(persona_id=p.id, rol="admin"))
    db.add(ProcesoEspiritual(persona_id=p.id, etapa="visitante"))
    pin = "0000"
    db.add(Usuario(persona_id=p.id, pin_hash=hashear_pin(pin), debe_cambiar_pin=True))
    db.commit()
    return JSONResponse({
        "ok": True,
        "mensaje": "Admin creado. Entra con DNI: 00000001 y PIN: 0000. Cambia el PIN inmediatamente.",
        "dni": dni,
        "pin": pin,
    })


@app.get("/admin/sync-db")
async def admin_sync_db(request: Request):
    u, redir = require_admin(request)
    if redir:
        return redir
    global _db_ready
    try:
        init_db()
        _db_ready = True
        return JSONResponse({"ok": True, "message": "Base de datos sincronizada correctamente"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ── helpers de template ───────────────────────────────────────────────────────
def ctx(request: Request, **kwargs):
    u = get_session_user(request)
    return {"request": request, "session_user": u, "ROLES": ROLES, **kwargs}


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/login")
async def login_get(request: Request):
    if get_session_user(request):
        return RedirectResponse(url="/dashboard", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"request": request, "error": None})


@app.post("/login")
async def login_post(
    request: Request,
    dni: str = Form(...),
    pin: str = Form(...),
    db: Session = Depends(get_db),
):
    persona = db.query(Persona).filter(Persona.dni == dni.strip(), Persona.activo == True).first()
    if not persona or not persona.usuario:
        return templates.TemplateResponse(request, "login.html", {"request": request, "error": "DNI no encontrado o sin acceso al sistema."})
    if not verificar_pin(pin.strip(), persona.usuario.pin_hash):
        return templates.TemplateResponse(request, "login.html", {"request": request, "error": "PIN incorrecto."})
    login_session(request, persona, persona.usuario, db)
    if persona.usuario.debe_cambiar_pin:
        return RedirectResponse(url="/cambiar-pin", status_code=303)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/logout")
async def logout(request: Request):
    logout_session(request)
    return RedirectResponse(url="/login", status_code=303)


@app.get("/cambiar-pin")
async def cambiar_pin_get(request: Request):
    u = get_session_user(request)
    if not u:
        return redirect_login()
    return templates.TemplateResponse(request, "cambiar_pin.html", {"request": request, "session_user": u, "error": None})


@app.post("/cambiar-pin")
async def cambiar_pin_post(
    request: Request,
    pin_nuevo: str = Form(...),
    pin_confirmar: str = Form(...),
    db: Session = Depends(get_db),
):
    u = get_session_user(request)
    if not u:
        return redirect_login()
    if pin_nuevo != pin_confirmar:
        return templates.TemplateResponse(request, "cambiar_pin.html", {"request": request, "session_user": u, "error": "Los PINs no coinciden."})
    if len(pin_nuevo) < 4:
        return templates.TemplateResponse(request, "cambiar_pin.html", {"request": request, "session_user": u, "error": "El PIN debe tener al menos 4 caracteres."})
    usuario = db.query(Usuario).filter(Usuario.persona_id == u["persona_id"]).first()
    usuario.pin_hash = hashear_pin(pin_nuevo)
    usuario.debe_cambiar_pin = False
    db.commit()
    request.session["usuario"]["debe_cambiar_pin"] = False
    return RedirectResponse(url="/dashboard", status_code=303)


# ═══════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/")
async def root(request: Request):
    u = get_session_user(request)
    return RedirectResponse(url="/dashboard" if u else "/login", status_code=303)


@app.get("/dashboard")
async def dashboard(request: Request, db: Session = Depends(get_db)):
    u = check_auth(request)
    if not u:
        return redirect_login()

    hoy = date.today()
    primer_dia_mes = hoy.replace(day=1)

    total_personas   = db.query(Persona).filter(Persona.activo == True).count()
    total_matriculas = db.query(Matricula).filter(Matricula.estado == "activo").count()
    total_celulas    = db.query(Celula).filter(Celula.activa == True).count()
    cobrado_mes      = sum(a.monto for a in db.query(Abono).filter(Abono.fecha >= primer_dia_mes).all())
    por_graduarse    = db.query(Matricula).filter(Matricula.estado == "por_graduarse").count()
    ultimos_reportes = db.query(ReporteCelula).order_by(ReporteCelula.created_at.desc()).limit(5).all()
    ultimos_abonos   = db.query(Abono).order_by(Abono.fecha.desc()).limit(5).all()

    # Gráfico: nuevas personas por mes (últimos 6 meses)
    seis_meses = hoy - timedelta(days=180)
    personas_por_mes: dict[str, int] = {}
    for p in db.query(Persona).filter(Persona.fecha_registro >= seis_meses).all():
        k = p.fecha_registro.strftime("%b %Y") if p.fecha_registro else None
        if k:
            personas_por_mes[k] = personas_por_mes.get(k, 0) + 1

    # Gráfico: distribución por etapa espiritual
    etapas_count: dict[str, int] = {}
    for e in db.query(ProcesoEspiritual).all():
        etapas_count[e.etapa] = etapas_count.get(e.etapa, 0) + 1

    # Gráfico: asistencia últimos 8 cultos
    ultimos_cultos = db.query(Culto).order_by(Culto.fecha.desc()).limit(8).all()
    cultos_labels = [f"{c.tipo[:3].title()} {c.fecha.strftime('%d/%m')}" for c in reversed(ultimos_cultos)]
    cultos_asistencia = [len(c.asistencias) for c in reversed(ultimos_cultos)]

    # Cumpleaños próximos 7 días
    cumples_proximos = []
    for p in db.query(Persona).filter(Persona.activo == True, Persona.fecha_nacimiento != None).all():
        fn = p.fecha_nacimiento
        try:
            este_anio = fn.replace(year=hoy.year)
        except ValueError:
            este_anio = fn.replace(year=hoy.year, day=28)
        if este_anio < hoy:
            try:
                este_anio = fn.replace(year=hoy.year + 1)
            except ValueError:
                este_anio = fn.replace(year=hoy.year + 1, day=28)
        dias = (este_anio - hoy).days
        if 0 <= dias <= 7:
            cumples_proximos.append({"persona": p, "dias": dias, "fecha": este_anio})
    cumples_proximos.sort(key=lambda x: x["dias"])

    # Próximos eventos
    proximos_eventos = (
        db.query(Evento).filter(Evento.fecha_inicio >= hoy)
        .order_by(Evento.fecha_inicio).limit(4).all()
    )

    return templates.TemplateResponse(request, "dashboard.html", ctx(
        request,
        total_personas=total_personas,
        total_matriculas=total_matriculas,
        total_celulas=total_celulas,
        cobrado_mes=cobrado_mes,
        por_graduarse=por_graduarse,
        ultimos_reportes=ultimos_reportes,
        ultimos_abonos=ultimos_abonos,
        personas_por_mes=json.dumps(personas_por_mes),
        etapas_count=json.dumps(etapas_count),
        cultos_labels=json.dumps(cultos_labels),
        cultos_asistencia=json.dumps(cultos_asistencia),
        cumples_proximos=cumples_proximos,
        proximos_eventos=proximos_eventos,
    ))


# ═══════════════════════════════════════════════════════════════════════════════
# PERSONAS
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/personas")
async def personas_lista(request: Request, q: str = "", db: Session = Depends(get_db)):
    u, redir = require_admin(request)
    if redir:
        return redir
    query = db.query(Persona)
    if q:
        query = query.filter(
            (Persona.nombre.ilike(f"%{q}%")) |
            (Persona.apellido.ilike(f"%{q}%")) |
            (Persona.dni.ilike(f"%{q}%"))
        )
    personas = query.order_by(Persona.nombre).all()
    return templates.TemplateResponse(request, "personas/lista.html", ctx(
        request, personas=personas, q=q,
    ))


@app.get("/personas/cumpleanos")
async def cumpleanos_early(request: Request, mes: int = 0, db: Session = Depends(get_db)):
    u = check_auth(request)
    if not u:
        return redirect_login()
    hoy = date.today()
    mes_actual = mes if 1 <= mes <= 12 else hoy.month
    todos = db.query(Persona).filter(Persona.activo == True, Persona.fecha_nacimiento != None).all()
    del_mes = []
    for p in todos:
        if p.fecha_nacimiento.month == mes_actual:
            try:
                cumple = p.fecha_nacimiento.replace(year=hoy.year)
            except ValueError:
                cumple = p.fecha_nacimiento.replace(year=hoy.year, day=28)
            if cumple < hoy:
                try:
                    cumple = p.fecha_nacimiento.replace(year=hoy.year + 1)
                except ValueError:
                    cumple = p.fecha_nacimiento.replace(year=hoy.year + 1, day=28)
            dias = (cumple - hoy).days
            edad = hoy.year - p.fecha_nacimiento.year
            del_mes.append({"persona": p, "dias": dias, "fecha": cumple, "edad": edad})
    del_mes.sort(key=lambda x: x["fecha"].day)
    MESES = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
             "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
    return templates.TemplateResponse(request, "personas/cumpleanos.html", ctx(
        request, del_mes=del_mes, mes_actual=mes_actual, MESES=MESES, hoy=hoy,
    ))


@app.get("/personas/nueva")
async def persona_nueva_get(request: Request, db: Session = Depends(get_db)):
    u, redir = require_admin(request)
    if redir:
        return redir
    return templates.TemplateResponse(request, "personas/form.html", ctx(
        request, persona=None, ETAPAS=ETAPAS, ETAPAS_DISPLAY=ETAPAS_DISPLAY, error=None,
    ))


@app.post("/personas/nueva")
async def persona_nueva_post(
    request: Request,
    dni: str = Form(...),
    nombre: str = Form(...),
    apellido: str = Form(""),
    telefono: str = Form(""),
    email: str = Form(""),
    talla_polo: str = Form(""),
    fecha_nacimiento: str = Form(""),
    roles: list[str] = Form([]),
    etapa: str = Form("visitante"),
    crear_usuario: str = Form("no"),
    db: Session = Depends(get_db),
):
    u, redir = require_admin(request)
    if redir:
        return redir
    dni = dni.strip()
    if db.query(Persona).filter(Persona.dni == dni).first():
        return templates.TemplateResponse(request, "personas/form.html", ctx(
            request, persona=None, ETAPAS=ETAPAS, ETAPAS_DISPLAY=ETAPAS_DISPLAY,
            error=f"Ya existe una persona con DNI {dni}.",
        ))
    from datetime import date as date_type
    fn = None
    if fecha_nacimiento:
        try:
            fn = date_type.fromisoformat(fecha_nacimiento)
        except ValueError:
            pass
    p = Persona(dni=dni, nombre=nombre.strip(), apellido=apellido.strip(),
                telefono=telefono.strip(), email=email.strip(), talla_polo=talla_polo.strip(),
                fecha_nacimiento=fn)
    db.add(p)
    db.flush()

    for rol in roles:
        if rol in ROLES:
            db.add(PersonaRol(persona_id=p.id, rol=rol))

    db.add(ProcesoEspiritual(persona_id=p.id, etapa=etapa))

    if crear_usuario == "si" or roles:
        pin = pin_inicial(dni)
        db.add(Usuario(persona_id=p.id, pin_hash=hashear_pin(pin), debe_cambiar_pin=True))

    db.commit()
    return RedirectResponse(url=f"/personas/{p.id}", status_code=303)


@app.get("/personas/{persona_id}")
async def persona_detalle(request: Request, persona_id: int, db: Session = Depends(get_db)):
    u, redir = require_admin(request)
    if redir:
        return redir
    p = db.query(Persona).filter(Persona.id == persona_id).first()
    if not p:
        return RedirectResponse(url="/personas", status_code=303)
    # Trazabilidad
    asistencias = (
        db.query(AsistenciaLider)
        .filter(AsistenciaLider.persona_id == p.id)
        .order_by(AsistenciaLider.id.desc()).limit(20).all()
    )
    reportes_lider = (
        db.query(ReporteCelula)
        .filter(ReporteCelula.lider_dni == p.dni)
        .order_by(ReporteCelula.fecha.desc()).limit(20).all()
    ) if p.dni else []
    inscripciones_encuentro = (
        db.query(InscripcionEncuentro)
        .filter(InscripcionEncuentro.persona_id == p.id)
        .order_by(InscripcionEncuentro.fecha_inscripcion.desc()).all()
    )
    return templates.TemplateResponse(request, "personas/detalle.html", ctx(
        request, p=p, ETAPAS=ETAPAS, ETAPAS_DISPLAY=ETAPAS_DISPLAY,
        ROLES=ROLES, saldo_fn=saldo_matricula,
        asistencias=asistencias,
        reportes_lider=reportes_lider,
        inscripciones_encuentro=inscripciones_encuentro,
    ))


@app.post("/personas/{persona_id}/editar")
async def persona_editar(
    request: Request, persona_id: int,
    dni: str = Form(""),
    nombre: str = Form(...), apellido: str = Form(""),
    telefono: str = Form(""), email: str = Form(""),
    talla_polo: str = Form(""),
    db: Session = Depends(get_db),
):
    u, redir = require_admin(request)
    if redir:
        return redir
    p = db.query(Persona).filter(Persona.id == persona_id).first()
    if not p:
        return RedirectResponse(url="/personas", status_code=303)
    error = None
    dni = dni.strip()
    if dni and dni != p.dni:
        conflicto = db.query(Persona).filter(Persona.dni == dni, Persona.id != persona_id).first()
        if conflicto:
            error = f"El DNI {dni} ya está registrado para {conflicto.nombre_completo}."
        else:
            p.dni = dni
    if not error:
        p.nombre = nombre.strip(); p.apellido = apellido.strip()
        p.telefono = telefono.strip(); p.email = email.strip()
        p.talla_polo = talla_polo.strip()
        db.commit()
        return RedirectResponse(url=f"/personas/{persona_id}", status_code=303)
    return templates.TemplateResponse(request, "personas/detalle.html", ctx(
        request, p=p, ETAPAS=ETAPAS, ETAPAS_DISPLAY=ETAPAS_DISPLAY,
        ROLES=ROLES, saldo_fn=saldo_matricula, error=error,
    ))


@app.post("/personas/{persona_id}/roles")
async def persona_roles(
    request: Request, persona_id: int,
    roles: list[str] = Form([]),
    db: Session = Depends(get_db),
):
    u, redir = require_admin(request)
    if redir:
        return redir
    p = db.query(Persona).filter(Persona.id == persona_id).first()
    if not p:
        return RedirectResponse(url="/personas", status_code=303)

    # Marcar todos inactivos, luego activar los seleccionados
    for pr in p.roles:
        pr.activo = False
    for rol in roles:
        if rol in ROLES:
            existing = next((r for r in p.roles if r.rol == rol), None)
            if existing:
                existing.activo = True
            else:
                db.add(PersonaRol(persona_id=p.id, rol=rol))

    # Crear usuario si no tiene y tiene roles
    if roles and not p.usuario:
        db.add(Usuario(persona_id=p.id, pin_hash=hashear_pin(pin_inicial(p.dni)), debe_cambiar_pin=True))

    db.commit()
    return RedirectResponse(url=f"/personas/{persona_id}", status_code=303)


@app.post("/personas/{persona_id}/etapa")
async def persona_etapa(
    request: Request, persona_id: int,
    etapa: str = Form(...),
    db: Session = Depends(get_db),
):
    u, redir = require_admin(request)
    if redir:
        return redir
    p = db.query(Persona).filter(Persona.id == persona_id).first()
    if p:
        if not p.proceso:
            db.add(ProcesoEspiritual(persona_id=p.id, etapa=etapa))
        else:
            p.proceso.etapa = etapa
            p.proceso.fecha_inicio_etapa = datetime.utcnow()
        db.commit()
    return RedirectResponse(url=f"/personas/{persona_id}", status_code=303)


@app.post("/personas/{persona_id}/reset-pin")
async def persona_reset_pin(
    request: Request, persona_id: int,
    db: Session = Depends(get_db),
):
    u, redir = require_admin(request)
    if redir:
        return redir
    p = db.query(Persona).filter(Persona.id == persona_id).first()
    if p:
        if p.usuario:
            p.usuario.pin_hash = hashear_pin(pin_inicial(p.dni))
            p.usuario.debe_cambiar_pin = True
        else:
            db.add(Usuario(persona_id=p.id, pin_hash=hashear_pin(pin_inicial(p.dni)), debe_cambiar_pin=True))
        db.commit()
    return RedirectResponse(url=f"/personas/{persona_id}", status_code=303)


@app.post("/personas/{persona_id}/toggle")
async def persona_toggle(request: Request, persona_id: int, db: Session = Depends(get_db)):
    u, redir = require_admin(request)
    if redir:
        return redir
    p = db.query(Persona).filter(Persona.id == persona_id).first()
    if p:
        p.activo = not p.activo
        db.commit()
    return RedirectResponse(url=f"/personas/{persona_id}", status_code=303)


# ═══════════════════════════════════════════════════════════════════════════════
# ESCUELA
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/escuela")
async def escuela(request: Request, db: Session = Depends(get_db)):
    u, redir = require_roles(request, "maestro")
    if redir:
        return redir
    salones = db.query(Salon).order_by(Salon.activo.desc(), Salon.nivel).all()
    maestros = db.query(Persona).join(PersonaRol).filter(
        PersonaRol.rol == "maestro", PersonaRol.activo == True, Persona.activo == True,
    ).order_by(Persona.nombre).all()
    return templates.TemplateResponse(request, "escuela/salones.html", ctx(
        request, salones=salones, maestros=maestros,
    ))


@app.post("/escuela/salones")
async def salon_crear(
    request: Request,
    nivel: int = Form(...), nombre: str = Form(""),
    maestro_id: str = Form(""), fecha_inicio: str = Form(""),
    db: Session = Depends(get_db),
):
    u, redir = require_roles(request, "maestro")
    if redir:
        return redir
    db.add(Salon(
        nivel=nivel, nombre=nombre.strip(),
        maestro_id=int(maestro_id) if maestro_id else None,
        fecha_inicio=fecha_inicio,
    ))
    db.commit()
    return RedirectResponse(url="/escuela", status_code=303)


@app.get("/escuela/salones/{salon_id}")
async def salon_detalle(request: Request, salon_id: int, db: Session = Depends(get_db)):
    u, redir = require_roles(request, "maestro")
    if redir:
        return redir
    salon = db.query(Salon).filter(Salon.id == salon_id).first()
    if not salon:
        return RedirectResponse(url="/escuela", status_code=303)

    is_maestro = u and "admin" not in u["roles"] and "maestro" in u["roles"]
    if is_maestro:
        persona_actual = db.query(Persona).filter(Persona.dni == u["dni"]).first()
        if persona_actual and salon.maestro_id != persona_actual.id:
            return RedirectResponse(url="/escuela", status_code=303)

    return templates.TemplateResponse(request, "escuela/salon_detalle.html", ctx(
        request, salon=salon, saldo_fn=saldo_matricula,
    ))


@app.post("/escuela/salones/{salon_id}/matricular")
async def matricular(
    request: Request, salon_id: int,
    dni: str = Form(...), saldo_anterior: float = Form(0.0),
    db: Session = Depends(get_db),
):
    u, redir = require_roles(request, "maestro")
    if redir:
        return redir
    salon = db.query(Salon).filter(Salon.id == salon_id).first()
    if not salon:
        return RedirectResponse(url="/escuela", status_code=303)

    dni = dni.strip()
    persona = db.query(Persona).filter(Persona.dni == dni).first()
    if not persona:
        return templates.TemplateResponse(request, "escuela/salon_detalle.html", ctx(
            request, salon=salon, saldo_fn=saldo_matricula,
            error=f"No existe ninguna persona con DNI {dni}. Regístrala primero en Personas.",
        ))

    ya_matriculado = db.query(Matricula).filter(
        Matricula.persona_id == persona.id,
        Matricula.salon_id == salon_id,
        Matricula.estado == "activo",
    ).first()
    if ya_matriculado:
        return templates.TemplateResponse(request, "escuela/salon_detalle.html", ctx(
            request, salon=salon, saldo_fn=saldo_matricula,
            error=f"{persona.nombre_completo} ya está matriculado en este salón.",
        ))

    m = Matricula(persona_id=persona.id, salon_id=salon_id)
    db.add(m); db.flush()

    for c in CONCEPTOS_NIVEL:
        db.add(ConceptoPago(matricula_id=m.id, **c))
    if saldo_anterior > 0:
        db.add(ConceptoPago(matricula_id=m.id, concepto="Saldo anterior", mes=0, monto=round(saldo_anterior, 2)))

    db.add(HistorialSalon(matricula_id=m.id, salon_id=salon_id, motivo="Matrícula inicial"))
    db.commit()
    return RedirectResponse(url=f"/escuela/salones/{salon_id}", status_code=303)


@app.get("/escuela/matriculas/{matricula_id}")
async def matricula_detalle(request: Request, matricula_id: int, db: Session = Depends(get_db)):
    u, redir = require_roles(request, "maestro")
    if redir:
        return redir
    m = db.query(Matricula).filter(Matricula.id == matricula_id).first()
    if not m:
        return RedirectResponse(url="/escuela", status_code=303)
    saldo = saldo_matricula(m)
    salones = db.query(Salon).filter(Salon.activo == True).order_by(Salon.nivel).all()
    return templates.TemplateResponse(request, "escuela/matricula_detalle.html", ctx(
        request, m=m, saldo=saldo, salones=salones,
        ESTADOS=["activo", "por_graduarse", "graduado", "retirado"],
    ))


@app.post("/escuela/matriculas/{matricula_id}/abono")
async def matricula_abono(
    request: Request, matricula_id: int,
    monto: float = Form(...), metodo: str = Form("efectivo"), notas: str = Form(""),
    db: Session = Depends(get_db),
):
    u, redir = require_roles(request, "maestro")
    if redir:
        return redir
    db.add(Abono(matricula_id=matricula_id, monto=round(monto, 2), metodo=metodo, notas=notas.strip()))
    db.commit()
    return RedirectResponse(url=f"/escuela/matriculas/{matricula_id}", status_code=303)


@app.post("/escuela/abonos/{abono_id}/eliminar")
async def abono_eliminar(request: Request, abono_id: int, db: Session = Depends(get_db)):
    u, redir = require_roles(request, "maestro")
    if redir:
        return redir
    a = db.query(Abono).filter(Abono.id == abono_id).first()
    mid = a.matricula_id if a else None
    if a:
        db.delete(a); db.commit()
    return RedirectResponse(url=f"/escuela/matriculas/{mid}", status_code=303)


@app.post("/escuela/matriculas/{matricula_id}/nota")
async def matricula_nota(
    request: Request, matricula_id: int,
    asistencia: str = Form(""), uniforme: str = Form(""),
    devocional: str = Form(""), examen_oral: str = Form(""),
    control_libro: str = Form(""), examenes_mensuales: str = Form(""),
    exposicion: str = Form(""), trabajo_final: str = Form(""),
    db: Session = Depends(get_db),
):
    u, redir = require_roles(request, "maestro")
    if redir:
        return redir

    def parse(v): return float(v) if v.strip() else None

    m = db.query(Matricula).filter(Matricula.id == matricula_id).first()
    if not m:
        return RedirectResponse(url="/escuela", status_code=303)
    if not m.nota:
        m.nota = Nota(matricula_id=matricula_id)
        db.add(m.nota); db.flush()

    m.nota.asistencia = parse(asistencia); m.nota.uniforme = parse(uniforme)
    m.nota.devocional = parse(devocional); m.nota.examen_oral = parse(examen_oral)
    m.nota.control_libro = parse(control_libro); m.nota.examenes_mensuales = parse(examenes_mensuales)
    m.nota.exposicion = parse(exposicion); m.nota.trabajo_final = parse(trabajo_final)
    m.nota.nota_final = calcular_nota_final(m.nota)
    db.commit()
    return RedirectResponse(url=f"/escuela/matriculas/{matricula_id}", status_code=303)


@app.post("/escuela/matriculas/{matricula_id}/estado")
async def matricula_estado(
    request: Request, matricula_id: int,
    estado: str = Form(...), db: Session = Depends(get_db),
):
    u, redir = require_roles(request, "maestro")
    if redir:
        return redir
    m = db.query(Matricula).filter(Matricula.id == matricula_id).first()
    if m:
        m.estado = estado; db.commit()
    return RedirectResponse(url=f"/escuela/matriculas/{matricula_id}", status_code=303)


@app.post("/escuela/matriculas/{matricula_id}/cambiar-salon")
async def matricula_cambiar_salon(
    request: Request, matricula_id: int,
    nuevo_salon_id: int = Form(...), motivo: str = Form(""),
    db: Session = Depends(get_db),
):
    u, redir = require_roles(request, "maestro")
    if redir:
        return redir
    m = db.query(Matricula).filter(Matricula.id == matricula_id).first()
    if m:
        hist_actual = db.query(HistorialSalon).filter(
            HistorialSalon.matricula_id == matricula_id,
            HistorialSalon.fecha_salida == None,
        ).first()
        if hist_actual:
            hist_actual.fecha_salida = datetime.utcnow()
        m.salon_id = nuevo_salon_id
        db.add(HistorialSalon(matricula_id=m.id, salon_id=nuevo_salon_id, motivo=motivo.strip()))
        db.commit()
    return RedirectResponse(url=f"/escuela/matriculas/{matricula_id}", status_code=303)


# Vista estudiante: ver sus propias notas
@app.get("/mis-notas")
async def mis_notas(request: Request, db: Session = Depends(get_db)):
    u = check_auth(request)
    if not u:
        return redirect_login()
    persona = db.query(Persona).filter(Persona.dni == u["dni"]).first()
    matriculas = db.query(Matricula).filter(Matricula.persona_id == persona.id).all() if persona else []
    return templates.TemplateResponse(request, "escuela/mis_notas.html", ctx(
        request, matriculas=matriculas, saldo_fn=saldo_matricula,
    ))


# ═══════════════════════════════════════════════════════════════════════════════
# CÉLULAS
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/celulas")
async def celulas_lista(request: Request, db: Session = Depends(get_db)):
    u, redir = require_roles(request, "lider_g1", "lider_g2")
    if redir:
        return redir
    reportes = db.query(ReporteCelula).order_by(ReporteCelula.created_at.desc()).limit(50).all()
    celulas  = db.query(Celula).filter(Celula.activa == True).order_by(Celula.nombre_casa).all()
    return templates.TemplateResponse(request, "celulas/lista.html", ctx(
        request, reportes=reportes, celulas=celulas,
    ))


@app.get("/celulas/formulario")
async def celula_formulario(request: Request, db: Session = Depends(get_db)):
    celulas = db.query(Celula).filter(Celula.activa == True).order_by(Celula.nombre_casa).all()
    return templates.TemplateResponse(request, "celulas/formulario.html", {
        "request": request, "celulas": celulas,
    })


@app.post("/api/celulas/reporte")
async def celula_reporte_api(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    lider_dni = data.get("lider_dni", "").strip()

    celula = None
    lider_nombre = ""
    lider_obj = None
    if lider_dni:
        lider_obj = db.query(Persona).filter(Persona.dni == lider_dni).first()
        if lider_obj:
            lider_nombre = lider_obj.nombre_completo
            if lider_obj.celulas:
                celula = lider_obj.celulas[0]

    fecha_str = data.get("fecha_reunion", "") or data.get("fecha", "")
    try:
        fecha_obj = date.fromisoformat(fecha_str) if fecha_str else None
    except Exception:
        fecha_obj = None

    asistentes_data = data.get("asistentes", [])
    es_nueva = data.get("es_nueva_celula", False)
    if isinstance(es_nueva, str):
        es_nueva = es_nueva.lower() in ("si", "true", "1")

    total_nuevos = sum(1 for a in asistentes_data if a.get("tipo") == "nuevo" or a.get("nuevo"))

    reporte = ReporteCelula(
        celula_id=celula.id if celula else None,
        lider_dni=lider_dni,
        lider_nombre=lider_nombre,
        tema=data.get("tema", ""),
        fecha=fecha_obj,
        hora=data.get("hora_inicio", "") or data.get("hora", ""),
        direccion=data.get("direccion", "") or data.get("dir", ""),
        tipo=data.get("tipo_celula", "") or data.get("celula", "abierta"),
        modalidad=data.get("modalidad", "presencial"),
        es_nueva=es_nueva,
        ofrenda=float(data.get("ofrenda") or 0),
        observaciones=data.get("observaciones", "") or data.get("obs", ""),
        total_asistentes=len(asistentes_data),
        total_nuevos=total_nuevos,
    )
    db.add(reporte); db.flush()

    for a in asistentes_data:
        nombre_a = a.get("nombre", "").strip()
        es_nuevo = a.get("tipo") == "nuevo" or bool(a.get("nuevo"))
        # Si trae DNI, buscar/crear persona para trazabilidad
        dni_a = a.get("dni", "").strip()
        persona_a = None
        if dni_a and nombre_a:
            persona_a = db.query(Persona).filter(Persona.dni == dni_a).first()
            if not persona_a:
                # Creación rápida para regularizar después
                persona_a = Persona(dni=dni_a, nombre=nombre_a, apellido="")
                db.add(persona_a); db.flush()
                db.add(ProcesoEspiritual(persona_id=persona_a.id, etapa="visitante"))

        db.add(AsistenteCelula(
            reporte_id=reporte.id,
            nombre=nombre_a,
            es_nuevo=es_nuevo,
            celular=a.get("telefono", "") or a.get("celular", ""),
            direccion=a.get("como_llego", "") or a.get("direccion", ""),
        ))
    db.commit()
    return JSONResponse({"ok": True, "reporte_id": reporte.id})


# ═══════════════════════════════════════════════════════════════════════════════
# LIDERAZGO — CHECKIN REUNIÓN DE LÍDERES
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/liderazgo/reuniones")
async def reuniones_lista(request: Request, db: Session = Depends(get_db)):
    u, redir = require_roles(request, "lider_g1", "lider_g2")
    if redir:
        return redir
    reuniones = db.query(ReunionLideres).order_by(ReunionLideres.fecha.desc()).limit(20).all()
    return templates.TemplateResponse(request, "liderazgo/reuniones.html", ctx(
        request, reuniones=reuniones,
    ))


@app.post("/liderazgo/reuniones")
async def reunion_crear(
    request: Request,
    descripcion: str = Form("Reunión de líderes"),
    db: Session = Depends(get_db),
):
    u, redir = require_roles(request, "lider_g1", "lider_g2")
    if redir:
        return redir
    persona = db.query(Persona).filter(Persona.dni == u["dni"]).first()
    r = ReunionLideres(
        fecha=date.today(),
        descripcion=descripcion.strip(),
        creada_por_id=persona.id if persona else None,
    )
    db.add(r); db.commit()
    return RedirectResponse(url=f"/liderazgo/checkin/{r.id}", status_code=303)


@app.get("/liderazgo/checkin/{reunion_id}")
async def checkin_get(request: Request, reunion_id: int, db: Session = Depends(get_db)):
    reunion = db.query(ReunionLideres).filter(ReunionLideres.id == reunion_id).first()
    if not reunion:
        return RedirectResponse(url="/liderazgo/reuniones", status_code=303)
    return templates.TemplateResponse(request, "liderazgo/checkin.html", {
        "request": request, "reunion": reunion,
    })


@app.post("/liderazgo/checkin/{reunion_id}")
async def checkin_post(
    request: Request, reunion_id: int,
    dni: str = Form(...), db: Session = Depends(get_db),
):
    reunion = db.query(ReunionLideres).filter(ReunionLideres.id == reunion_id).first()
    if not reunion:
        return JSONResponse({"ok": False, "error": "Reunión no encontrada."})

    dni = dni.strip()
    persona = db.query(Persona).filter(Persona.dni == dni, Persona.activo == True).first()
    if not persona:
        return JSONResponse({"ok": False, "error": f"DNI {dni} no está registrado en el sistema."})

    ya = db.query(AsistenciaLider).filter(
        AsistenciaLider.reunion_id == reunion_id,
        AsistenciaLider.persona_id == persona.id,
    ).first()
    if ya:
        return JSONResponse({"ok": True, "ya_registrado": True, "nombre": persona.nombre_completo})

    hora = datetime.utcnow().strftime("%H:%M")
    db.add(AsistenciaLider(reunion_id=reunion_id, persona_id=persona.id, hora_entrada=hora))
    db.commit()
    return JSONResponse({"ok": True, "ya_registrado": False, "nombre": persona.nombre_completo, "hora": hora})


# ═══════════════════════════════════════════════════════════════════════════════
# OFRENDAS
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/ofrendas")
async def ofrendas(request: Request, db: Session = Depends(get_db)):
    u, redir = require_roles(request, "tesorero")
    if redir:
        return redir
    cultos = db.query(Culto).order_by(Culto.fecha.desc()).limit(30).all()
    siembras = db.query(SiembraCocina).order_by(SiembraCocina.fecha.desc()).limit(10).all()
    return templates.TemplateResponse(request, "ofrendas/lista.html", ctx(
        request, cultos=cultos, siembras=siembras,
    ))


@app.post("/ofrendas/culto")
async def culto_crear(
    request: Request,
    tipo: str = Form(...), fecha: str = Form(...), descripcion: str = Form(""),
    db: Session = Depends(get_db),
):
    u, redir = require_roles(request, "tesorero")
    if redir:
        return redir
    c = Culto(tipo=tipo, fecha=date.fromisoformat(fecha), descripcion=descripcion.strip())
    db.add(c); db.commit()
    return RedirectResponse(url=f"/ofrendas/{c.id}", status_code=303)


@app.get("/ofrendas/{culto_id}")
async def culto_detalle(request: Request, culto_id: int, db: Session = Depends(get_db)):
    u, redir = require_roles(request, "tesorero")
    if redir:
        return redir
    culto = db.query(Culto).filter(Culto.id == culto_id).first()
    if not culto:
        return RedirectResponse(url="/ofrendas", status_code=303)
    return templates.TemplateResponse(request, "ofrendas/culto_detalle.html", ctx(
        request, culto=culto,
    ))


@app.post("/ofrendas/{culto_id}/registrar")
async def ofrenda_registrar(
    request: Request, culto_id: int,
    monto: float = Form(...), concepto: str = Form("ofrenda"),
    db: Session = Depends(get_db),
):
    u, redir = require_roles(request, "tesorero")
    if redir:
        return redir
    persona = db.query(Persona).filter(Persona.dni == u["dni"]).first()
    hora = datetime.utcnow().strftime("%H:%M")
    db.add(Ofrenda(
        culto_id=culto_id, monto=round(monto, 2), concepto=concepto,
        registrado_por_id=persona.id if persona else None, hora=hora,
    ))
    db.commit()
    return RedirectResponse(url=f"/ofrendas/{culto_id}", status_code=303)


@app.post("/ofrendas/siembra")
async def siembra_registrar(
    request: Request,
    fecha: str = Form(...), monto: float = Form(...), descripcion: str = Form(""),
    db: Session = Depends(get_db),
):
    u, redir = require_roles(request, "tesorero")
    if redir:
        return redir
    persona = db.query(Persona).filter(Persona.dni == u["dni"]).first()
    db.add(SiembraCocina(
        fecha=date.fromisoformat(fecha), monto=round(monto, 2),
        descripcion=descripcion.strip(),
        registrado_por_id=persona.id if persona else None,
    ))
    db.commit()
    return RedirectResponse(url="/ofrendas", status_code=303)


# ═══════════════════════════════════════════════════════════════════════════════
# API — verificar persona por DNI (para el formulario de células)
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/personas/dni/{dni}")
async def api_persona_dni(dni: str, db: Session = Depends(get_db)):
    p = db.query(Persona).filter(Persona.dni == dni.strip(), Persona.activo == True).first()
    if not p:
        return JSONResponse({"encontrado": False})
    return JSONResponse({
        "encontrado": True,
        "nombre": p.nombre_completo,
        "celula": p.celulas[0].nombre_casa if p.celulas else None,
    })


@app.get("/api/reniec/{dni}")
async def reniec_consulta(dni: str):
    token = os.getenv("RENIEC_TOKEN", "")
    if not token:
        return JSONResponse({"ok": False, "error": "Sin token RENIEC configurado"}, status_code=503)
    if not dni.isdigit() or len(dni) != 8:
        return JSONResponse({"ok": False, "error": "DNI debe tener 8 dígitos"}, status_code=400)
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                f"https://api.decolecta.com/v1/reniec/dni?numero={dni}",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
        if r.status_code == 200:
            data = r.json()
            return JSONResponse({
                "ok": True,
                "nombre": data.get("first_name", ""),
                "apellido_paterno": data.get("first_last_name", ""),
                "apellido_materno": data.get("second_last_name", ""),
                "nombre_completo": data.get("full_name", ""),
            })
        return JSONResponse({"ok": False, "error": "DNI no encontrado en RENIEC"}, status_code=404)
    except Exception:
        return JSONResponse({"ok": False, "error": "Error al consultar RENIEC"}, status_code=502)


@app.post("/api/personas/rapido")
async def persona_rapida(request: Request, db: Session = Depends(get_db)):
    """Crea una persona con datos mínimos (DNI + nombre) para regularizar después."""
    data = await request.json()
    dni = data.get("dni", "").strip()
    nombre = data.get("nombre", "").strip()
    if not dni or not nombre:
        return JSONResponse({"ok": False, "error": "DNI y nombre son requeridos"}, status_code=400)

    existente = db.query(Persona).filter(Persona.dni == dni).first()
    if existente:
        return JSONResponse({
            "ok": True, "ya_existia": True,
            "persona_id": existente.id, "nombre": existente.nombre_completo,
        })

    p = Persona(dni=dni, nombre=nombre, apellido="")
    db.add(p); db.flush()
    db.add(ProcesoEspiritual(persona_id=p.id, etapa="visitante"))
    db.commit()
    return JSONResponse({
        "ok": True, "ya_existia": False,
        "persona_id": p.id, "nombre": p.nombre_completo,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# ENCUENTRO
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/encuentro")
async def encuentro_lista(request: Request, db: Session = Depends(get_db)):
    u, redir = require_roles(request, "lider_g1")
    if redir:
        return redir
    grupos = db.query(GrupoEncuentro).order_by(GrupoEncuentro.id.desc()).all()
    return templates.TemplateResponse(request, "encuentro/lista.html", ctx(
        request, grupos=grupos,
    ))


@app.post("/encuentro/nuevo")
async def encuentro_nuevo(
    request: Request,
    nombre: str = Form(...),
    fecha_apertura: str = Form(""),
    fecha_encuentro: str = Form(""),
    notas: str = Form(""),
    db: Session = Depends(get_db),
):
    u, redir = require_roles(request, "lider_g1")
    if redir:
        return redir
    encargado = db.query(Persona).filter(Persona.dni == u["dni"]).first()
    g = GrupoEncuentro(
        nombre=nombre.strip(),
        encargado_id=encargado.id if encargado else None,
        fecha_apertura=date.fromisoformat(fecha_apertura) if fecha_apertura else None,
        fecha_encuentro=date.fromisoformat(fecha_encuentro) if fecha_encuentro else None,
        notas=notas.strip(),
    )
    db.add(g); db.commit()
    return RedirectResponse(url=f"/encuentro/{g.id}", status_code=303)


@app.get("/encuentro/{grupo_id}")
async def encuentro_detalle(request: Request, grupo_id: int, db: Session = Depends(get_db)):
    u, redir = require_roles(request, "lider_g1")
    if redir:
        return redir
    g = db.query(GrupoEncuentro).filter(GrupoEncuentro.id == grupo_id).first()
    if not g:
        return RedirectResponse(url="/encuentro", status_code=303)
    total_pagado = sum(
        sum(pa.monto for pa in ins.pagos) for ins in g.inscripciones
    )
    return templates.TemplateResponse(request, "encuentro/detalle.html", ctx(
        request, g=g, total_pagado=total_pagado,
    ))


@app.post("/encuentro/{grupo_id}/inscribir")
async def encuentro_inscribir(
    request: Request, grupo_id: int,
    dni: str = Form(...),
    talla_polo: str = Form(""),
    db: Session = Depends(get_db),
):
    u, redir = require_roles(request, "lider_g1")
    if redir:
        return redir
    g = db.query(GrupoEncuentro).filter(GrupoEncuentro.id == grupo_id).first()
    if not g:
        return RedirectResponse(url="/encuentro", status_code=303)

    persona = db.query(Persona).filter(Persona.dni == dni.strip()).first()
    if not persona:
        return RedirectResponse(
            url=f"/encuentro/{grupo_id}?error=DNI+no+encontrado", status_code=303
        )
    ya = db.query(InscripcionEncuentro).filter(
        InscripcionEncuentro.grupo_id == grupo_id,
        InscripcionEncuentro.persona_id == persona.id,
    ).first()
    if ya:
        return RedirectResponse(
            url=f"/encuentro/{grupo_id}?error=Ya+inscrito", status_code=303
        )

    db.add(InscripcionEncuentro(
        persona_id=persona.id, grupo_id=grupo_id, talla_polo=talla_polo,
    ))
    # Actualizar etapa a pre_encuentro si está en etapa anterior
    if persona.proceso and persona.proceso.etapa in ("visitante", "pre_encuentro"):
        persona.proceso.etapa = "pre_encuentro"
    db.commit()
    return RedirectResponse(url=f"/encuentro/{grupo_id}?ok=Inscrito", status_code=303)


@app.post("/encuentro/inscripciones/{ins_id}/pago")
async def encuentro_pago(
    request: Request, ins_id: int,
    concepto: str = Form(""),
    monto: float = Form(...),
    metodo: str = Form("efectivo"),
    db: Session = Depends(get_db),
):
    u, redir = require_roles(request, "lider_g1")
    if redir:
        return redir
    ins = db.query(InscripcionEncuentro).filter(InscripcionEncuentro.id == ins_id).first()
    if not ins:
        return RedirectResponse(url="/encuentro", status_code=303)
    registrador = db.query(Persona).filter(Persona.dni == u["dni"]).first()
    db.add(PagoEncuentro(
        inscripcion_id=ins_id,
        concepto=concepto.strip() or "Pago encuentro",
        monto=monto,
        metodo=metodo,
        registrado_por_id=registrador.id if registrador else None,
    ))
    db.commit()
    return RedirectResponse(url=f"/encuentro/{ins.grupo_id}?ok=Pago+registrado", status_code=303)


@app.post("/encuentro/inscripciones/{ins_id}/completar")
async def encuentro_completar(request: Request, ins_id: int, db: Session = Depends(get_db)):
    u, redir = require_roles(request, "lider_g1")
    if redir:
        return redir
    ins = db.query(InscripcionEncuentro).filter(InscripcionEncuentro.id == ins_id).first()
    if not ins:
        return RedirectResponse(url="/encuentro", status_code=303)
    ins.estado = "completado"
    # Avanzar etapa espiritual
    if ins.persona and ins.persona.proceso:
        ins.persona.proceso.etapa = "pos_encuentro"
    # Agregar a cola de matrícula si no está ya pendiente
    ya_en_cola = db.query(PendienteMatricula).filter(
        PendienteMatricula.persona_id == ins.persona_id,
        PendienteMatricula.estado == "pendiente",
    ).first()
    if not ya_en_cola and ins.persona_id:
        db.add(PendienteMatricula(
            persona_id=ins.persona_id,
            inscripcion_encuentro_id=ins.id,
            nivel_sugerido="Nivel 1",
        ))
    db.commit()
    return RedirectResponse(url=f"/encuentro/{ins.grupo_id}?ok=Completado+%E2%80%94+persona+en+cola+de+matr%C3%ADcula", status_code=303)


@app.post("/encuentro/inscripciones/{ins_id}/retirar")
async def encuentro_retirar(request: Request, ins_id: int, db: Session = Depends(get_db)):
    u, redir = require_roles(request, "lider_g1")
    if redir:
        return redir
    ins = db.query(InscripcionEncuentro).filter(InscripcionEncuentro.id == ins_id).first()
    if not ins:
        return RedirectResponse(url="/encuentro", status_code=303)
    ins.estado = "retirado"
    db.commit()
    return RedirectResponse(url=f"/encuentro/{ins.grupo_id}?ok=Retirado", status_code=303)


@app.post("/encuentro/{grupo_id}/estado")
async def encuentro_estado(
    request: Request, grupo_id: int,
    estado: str = Form(...),
    db: Session = Depends(get_db),
):
    u, redir = require_roles(request, "lider_g1")
    if redir:
        return redir
    g = db.query(GrupoEncuentro).filter(GrupoEncuentro.id == grupo_id).first()
    if g:
        g.estado = estado
        db.commit()
    return RedirectResponse(url=f"/encuentro/{grupo_id}", status_code=303)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# ASISTENCIA A CULTOS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@app.get("/cultos/{culto_id}/asistencia")
async def culto_asistencia_get(request: Request, culto_id: int, db: Session = Depends(get_db)):
    culto = db.query(Culto).filter(Culto.id == culto_id).first()
    if not culto:
        return RedirectResponse(url="/ofrendas", status_code=303)
    asistencias = db.query(AsistenciaCulto).filter(AsistenciaCulto.culto_id == culto_id).all()
    return templates.TemplateResponse(request, "cultos/asistencia.html", {
        "request": request, "culto": culto, "asistencias": asistencias,
    })


@app.post("/cultos/{culto_id}/asistencia")
async def culto_asistencia_post(request: Request, culto_id: int, db: Session = Depends(get_db)):
    data = await request.json()
    dni = data.get("dni", "").strip()
    culto = db.query(Culto).filter(Culto.id == culto_id).first()
    if not culto:
        return JSONResponse({"ok": False, "error": "Culto no encontrado"}, status_code=404)
    persona = db.query(Persona).filter(Persona.dni == dni).first() if dni else None
    if not persona:
        return JSONResponse({"ok": False, "error": "DNI no registrado"})
    ya = db.query(AsistenciaCulto).filter(
        AsistenciaCulto.culto_id == culto_id,
        AsistenciaCulto.persona_id == persona.id,
    ).first()
    if ya:
        return JSONResponse({"ok": True, "ya_registrado": True, "nombre": persona.nombre_completo})
    db.add(AsistenciaCulto(culto_id=culto_id, persona_id=persona.id, tipo="miembro"))
    db.commit()
    total = db.query(AsistenciaCulto).filter(AsistenciaCulto.culto_id == culto_id).count()
    return JSONResponse({"ok": True, "ya_registrado": False, "nombre": persona.nombre_completo, "total": total})


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# CUMPLEANOS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@app.get("/personas/cumpleanos")
async def cumpleanos(request: Request, mes: int = 0, db: Session = Depends(get_db)):
    u = check_auth(request)
    if not u:
        return redirect_login()
    hoy = date.today()
    mes_actual = mes if 1 <= mes <= 12 else hoy.month
    todos = db.query(Persona).filter(Persona.activo == True, Persona.fecha_nacimiento != None).all()
    del_mes = []
    for p in todos:
        if p.fecha_nacimiento.month == mes_actual:
            try:
                cumple = p.fecha_nacimiento.replace(year=hoy.year)
            except ValueError:
                cumple = p.fecha_nacimiento.replace(year=hoy.year, day=28)
            if cumple < hoy:
                try:
                    cumple = p.fecha_nacimiento.replace(year=hoy.year + 1)
                except ValueError:
                    cumple = p.fecha_nacimiento.replace(year=hoy.year + 1, day=28)
            dias = (cumple - hoy).days
            edad = hoy.year - p.fecha_nacimiento.year
            del_mes.append({"persona": p, "dias": dias, "fecha": cumple, "edad": edad})
    del_mes.sort(key=lambda x: x["fecha"].day)
    MESES = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
             "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
    return templates.TemplateResponse(request, "personas/cumpleanos.html", ctx(
        request, del_mes=del_mes, mes_actual=mes_actual, MESES=MESES, hoy=hoy,
    ))


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# RED G12
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@app.get("/g12")
async def g12_arbol(request: Request, db: Session = Depends(get_db)):
    u = check_auth(request)
    if not u:
        return redirect_login()
    return templates.TemplateResponse(request, "g12/arbol.html", ctx(request))


@app.get("/api/g12/arbol")
async def g12_arbol_json(request: Request, db: Session = Depends(get_db)):
    u = check_auth(request)
    if not u:
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    lideres = db.query(LiderG12).filter(LiderG12.activo == True).all()
    nodes = {}
    for l in lideres:
        celulas_count = db.query(Celula).filter(Celula.lider_id == l.persona_id, Celula.activa == True).count()
        nodes[l.persona_id] = {
            "id": l.persona_id,
            "nombre": l.persona.nombre_completo if l.persona else f"#{l.persona_id}",
            "generacion": l.generacion,
            "supervisor_id": l.supervisor_id,
            "celulas": celulas_count,
            "etapa": l.persona.proceso.etapa if l.persona and l.persona.proceso else "",
            "children": [],
        }
    roots = []
    for l in lideres:
        node = nodes[l.persona_id]
        if l.supervisor_id and l.supervisor_id in nodes:
            nodes[l.supervisor_id]["children"].append(node)
        elif not l.supervisor_id:
            roots.append(node)
    return JSONResponse(roots)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# AGENDA
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@app.get("/agenda")
async def agenda(request: Request, mes: int = 0, anio: int = 0, db: Session = Depends(get_db)):
    u = check_auth(request)
    if not u:
        return redirect_login()
    import calendar as cal_mod
    hoy = date.today()
    mes_actual = mes if 1 <= mes <= 12 else hoy.month
    anio_actual = anio if anio >= 2020 else hoy.year
    primer_dia = date(anio_actual, mes_actual, 1)
    ultimo_dia = date(anio_actual, mes_actual, cal_mod.monthrange(anio_actual, mes_actual)[1])
    eventos = db.query(Evento).filter(
        Evento.fecha_inicio >= primer_dia,
        Evento.fecha_inicio <= ultimo_dia,
    ).order_by(Evento.fecha_inicio).all()
    primer_semana_dia = primer_dia.weekday()
    MESES = ["","Enero","Febrero","Marzo","Abril","Mayo","Junio",
             "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
    return templates.TemplateResponse(request, "agenda/lista.html", ctx(
        request,
        eventos=eventos,
        mes=mes_actual, anio=anio_actual,
        hoy=hoy,
        primer_dia=primer_dia,
        ultimo_dia=ultimo_dia,
        dias_mes=ultimo_dia.day,
        primer_semana_dia=primer_semana_dia,
        MESES=MESES,
        mes_anterior=(primer_dia - timedelta(days=1)),
        mes_siguiente=(ultimo_dia + timedelta(days=1)),
    ))


@app.post("/agenda/nuevo")
async def agenda_nuevo(
    request: Request,
    titulo: str = Form(...),
    descripcion: str = Form(""),
    fecha_inicio: str = Form(...),
    fecha_fin: str = Form(""),
    hora_inicio: str = Form(""),
    hora_fin: str = Form(""),
    tipo: str = Form("general"),
    lugar: str = Form(""),
    db: Session = Depends(get_db),
):
    u = check_auth(request)
    if not u:
        return redirect_login()
    responsable = db.query(Persona).filter(Persona.dni == u["dni"]).first()
    ev = Evento(
        titulo=titulo.strip(),
        descripcion=descripcion.strip(),
        fecha_inicio=date.fromisoformat(fecha_inicio),
        fecha_fin=date.fromisoformat(fecha_fin) if fecha_fin else None,
        hora_inicio=hora_inicio, hora_fin=hora_fin,
        tipo=tipo, lugar=lugar.strip(),
        responsable_id=responsable.id if responsable else None,
    )
    db.add(ev); db.commit()
    return RedirectResponse(url=f"/agenda?mes={ev.fecha_inicio.month}&anio={ev.fecha_inicio.year}", status_code=303)


@app.post("/agenda/{evento_id}/eliminar")
async def agenda_eliminar(request: Request, evento_id: int, db: Session = Depends(get_db)):
    u, redir = require_admin(request)
    if redir:
        return redir
    ev = db.query(Evento).filter(Evento.id == evento_id).first()
    if ev:
        mes, anio = ev.fecha_inicio.month, ev.fecha_inicio.year
        db.delete(ev); db.commit()
        return RedirectResponse(url=f"/agenda?mes={mes}&anio={anio}", status_code=303)
    return RedirectResponse(url="/agenda", status_code=303)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# EXPORTACION
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _xlsx_response(wb, filename: str) -> StreamingResponse:
    buf = io.BytesIO()
    wb.save(buf); buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/export/personas")
async def export_personas(request: Request, db: Session = Depends(get_db)):
    u, redir = require_admin(request)
    if redir:
        return redir
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Personas"
    ws.append(["DNI","Nombre","Apellido","Telefono","Email","Etapa","Roles","F. Nacimiento","Registro"])
    for p in db.query(Persona).order_by(Persona.apellido).all():
        roles = ", ".join(r.rol for r in p.roles if r.activo)
        etapa = p.proceso.etapa if p.proceso else ""
        ws.append([
            p.dni, p.nombre, p.apellido, p.telefono or "", p.email or "",
            etapa, roles,
            p.fecha_nacimiento.strftime("%d/%m/%Y") if p.fecha_nacimiento else "",
            p.fecha_registro.strftime("%d/%m/%Y") if p.fecha_registro else "",
        ])
    return _xlsx_response(wb, "personas.xlsx")


@app.get("/export/celulas")
async def export_celulas(request: Request, db: Session = Depends(get_db)):
    u, redir = require_roles(request, "lider_g1")
    if redir:
        return redir
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Reportes Celula"
    ws.append(["Fecha","Lider","Celula","Tema","Modalidad","Asistentes","Nuevos","Ofrenda"])
    for r in db.query(ReporteCelula).order_by(ReporteCelula.fecha.desc()).all():
        ws.append([
            r.fecha.strftime("%d/%m/%Y") if r.fecha else "",
            r.lider_nombre or "", r.celula.nombre_casa if r.celula else "",
            r.tema or "", r.modalidad or "",
            r.total_asistentes or 0, r.total_nuevos or 0,
            r.ofrenda or 0,
        ])
    return _xlsx_response(wb, "reportes_celula.xlsx")


@app.get("/export/ofrendas")
async def export_ofrendas(request: Request, db: Session = Depends(get_db)):
    u, redir = require_roles(request, "tesorero")
    if redir:
        return redir
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Ofrendas"
    ws.append(["Fecha","Tipo Culto","Concepto","Monto","Registrado por"])
    for o in db.query(Ofrenda).order_by(Ofrenda.id.desc()).all():
        ws.append([
            o.culto.fecha.strftime("%d/%m/%Y") if o.culto else "",
            o.culto.tipo if o.culto else "",
            o.concepto or "", o.monto or 0,
            o.registrado_por.nombre_completo if o.registrado_por else "",
        ])
    ws2 = wb.create_sheet("Siembra Cocina")
    ws2.append(["Fecha","Descripcion","Monto"])
    for s in db.query(SiembraCocina).order_by(SiembraCocina.fecha.desc()).all():
        ws2.append([s.fecha.strftime("%d/%m/%Y"), s.descripcion or "", s.monto or 0])
    return _xlsx_response(wb, "ofrendas.xlsx")


@app.get("/export/escuela")
async def export_escuela(request: Request, db: Session = Depends(get_db)):
    u, redir = require_admin(request)
    if redir:
        return redir
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Matriculas"
    ws.append(["DNI","Alumno","Salon","Estado","Ingreso","Deuda","Abonado","Saldo"])
    for m in db.query(Matricula).order_by(Matricula.id.desc()).all():
        s = saldo_matricula(m)
        ws.append([
            m.persona.dni if m.persona else "",
            m.persona.nombre_completo if m.persona else "",
            m.salon.nombre if m.salon else "",
            m.estado,
            m.fecha_ingreso.strftime("%d/%m/%Y") if m.fecha_ingreso else "",
            s["deuda"], s["abonado"], s["pendiente"],
        ])
    return _xlsx_response(wb, "escuela.xlsx")



# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# CHECKLIST OPERATIVO DE CULTOS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@app.get("/cultos/{culto_id}/checklist")
async def culto_checklist_get(request: Request, culto_id: int, db: Session = Depends(get_db)):
    u = check_auth(request)
    if not u:
        return redirect_login()
    culto = db.query(Culto).filter(Culto.id == culto_id).first()
    if not culto:
        return RedirectResponse(url="/ofrendas", status_code=303)
    areas = db.query(AreaServicio).filter(AreaServicio.activo == True).order_by(AreaServicio.orden).all()
    # Si no hay asignaciones aun, las creamos desde la plantilla
    if not culto.asignaciones:
        for area in areas:
            asig = AsignacionCulto(culto_id=culto_id, area_id=area.id)
            db.add(asig)
            db.flush()
            for item in area.items_plantilla:
                db.add(ChecklistItemCulto(
                    asignacion_id=asig.id,
                    descripcion=item.descripcion,
                    orden=item.orden,
                ))
        db.commit()
        db.refresh(culto)
    personas = db.query(Persona).filter(Persona.activo == True).order_by(Persona.nombre).all()
    return templates.TemplateResponse(request, "cultos/checklist.html", ctx(
        request, culto=culto, areas=areas, personas=personas,
    ))


@app.post("/cultos/{culto_id}/checklist/asignar")
async def culto_checklist_asignar(
    request: Request, culto_id: int,
    asignacion_id: int = Form(...),
    responsable_id: str = Form(""),
    notas: str = Form(""),
    db: Session = Depends(get_db),
):
    u = check_auth(request)
    if not u:
        return redirect_login()
    asig = db.query(AsignacionCulto).filter(
        AsignacionCulto.id == asignacion_id,
        AsignacionCulto.culto_id == culto_id,
    ).first()
    if asig:
        asig.responsable_id = int(responsable_id) if responsable_id else None
        asig.notas = notas.strip()
        db.commit()
    return RedirectResponse(url=f"/cultos/{culto_id}/checklist", status_code=303)


@app.post("/api/checklist/item/{item_id}/toggle")
async def checklist_item_toggle(request: Request, item_id: int, db: Session = Depends(get_db)):
    u = check_auth(request)
    if not u:
        return JSONResponse({"ok": False, "error": "No autenticado"}, status_code=401)
    item = db.query(ChecklistItemCulto).filter(ChecklistItemCulto.id == item_id).first()
    if not item:
        return JSONResponse({"ok": False, "error": "Item no encontrado"}, status_code=404)
    persona = db.query(Persona).filter(Persona.dni == u["dni"]).first()
    item.completado = not item.completado
    item.completado_por_id = persona.id if persona and item.completado else None
    item.completado_at = datetime.utcnow() if item.completado else None
    # Auto-actualizar estado de la asignacion
    asig = item.asignacion
    total = len(asig.items)
    done = sum(1 for i in asig.items if i.completado)
    if done == 0:
        asig.estado = "pendiente"
    elif done == total:
        asig.estado = "completado"
    else:
        asig.estado = "en_proceso"
    db.commit()
    return JSONResponse({"ok": True, "completado": item.completado, "asignacion_estado": asig.estado,
                         "done": done, "total": total})


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# COLA: ENCUENTRO -> ESCUELA
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@app.get("/escuela/pendientes-matricula")
async def pendientes_matricula_get(request: Request, db: Session = Depends(get_db)):
    u, redir = require_roles(request, "maestro")
    if redir:
        return redir
    pendientes = db.query(PendienteMatricula).filter(
        PendienteMatricula.estado == "pendiente"
    ).order_by(PendienteMatricula.created_at.desc()).all()
    salones = db.query(Salon).filter(Salon.activo == True).all()
    return templates.TemplateResponse(request, "escuela/pendientes_matricula.html", ctx(
        request, pendientes=pendientes, salones=salones,
    ))


@app.post("/escuela/pendientes-matricula/{pm_id}/matricular")
async def pendiente_matricular(
    request: Request, pm_id: int,
    salon_id: int = Form(...),
    notas: str = Form(""),
    db: Session = Depends(get_db),
):
    u, redir = require_roles(request, "maestro")
    if redir:
        return redir
    pm = db.query(PendienteMatricula).filter(PendienteMatricula.id == pm_id).first()
    if not pm:
        return RedirectResponse(url="/escuela/pendientes-matricula", status_code=303)
    salon = db.query(Salon).filter(Salon.id == salon_id).first()
    if not salon:
        return RedirectResponse(url="/escuela/pendientes-matricula?error=Salon+no+encontrado", status_code=303)
    # Verificar que no tenga ya matricula activa
    ya = db.query(Matricula).filter(
        Matricula.persona_id == pm.persona_id,
        Matricula.estado == "activo",
    ).first()
    if ya:
        pm.estado = "matriculado"
        pm.notas = f"Ya tenia matricula activa en {ya.salon.nombre if ya.salon else '?'}"
        db.commit()
        return RedirectResponse(url="/escuela/pendientes-matricula?ok=Ya+tenia+matricula+activa", status_code=303)
    matricula = Matricula(
        persona_id=pm.persona_id,
        salon_id=salon_id,
        estado="activo",
        fecha_ingreso=date.today(),
    )
    db.add(matricula)
    pm.estado = "matriculado"
    pm.notas = notas.strip()
    # Avanzar etapa si aun esta en pos_encuentro
    if pm.persona and pm.persona.proceso and pm.persona.proceso.etapa == "pos_encuentro":
        pm.persona.proceso.etapa = "discipulo"
    db.commit()
    return RedirectResponse(url=f"/escuela/pendientes-matricula?ok={pm.persona.nombre_completo}+matriculado+en+{salon.nombre}", status_code=303)


@app.post("/escuela/pendientes-matricula/{pm_id}/descartar")
async def pendiente_descartar(request: Request, pm_id: int, db: Session = Depends(get_db)):
    u, redir = require_roles(request, "maestro")
    if redir:
        return redir
    pm = db.query(PendienteMatricula).filter(PendienteMatricula.id == pm_id).first()
    if pm:
        pm.estado = "descartado"
        db.commit()
    return RedirectResponse(url="/escuela/pendientes-matricula", status_code=303)

