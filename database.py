from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean,
    DateTime, ForeignKey, Text, Date, UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

_url = os.getenv("DATABASE_URL", "")
print(f"[DB] DATABASE_URL prefix: {_url[:30] if _url else '(VACÍO)'}")

# Render/Railway usan postgres:// — SQLAlchemy necesita postgresql://
if _url.startswith("postgres://"):
    _url = _url.replace("postgres://", "postgresql://", 1)
# Agregar dialecto psycopg2 explícito
if _url.startswith("postgresql://") and "+" not in _url.split("//")[0]:
    _url = _url.replace("postgresql://", "postgresql+psycopg2://", 1)

if not _url:
    raise RuntimeError("DATABASE_URL no está configurada. Agrégala como variable de entorno en Render.")

print(f"[DB] URL final prefix: {_url[:35]}")
engine = create_engine(_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ─── Roles disponibles ────────────────────────────────────────────────────────
ROLES = {
    "admin":                "Administrador",
    "maestro":              "Maestro",
    "lider_g1":             "Líder G12 (1ª gen.)",
    "lider_g2":             "Líder G12 (2ª gen.)",
    "tesorero":             "Tesorero",
    "encargado_encuentro":  "Enc. Grupos de Encuentro",
    "encargado_pre_post":   "Enc. Pre/Post Encuentro",
    "miembro":              "Miembro",
}

ETAPAS = [
    "visitante", "grupo_encuentro", "pre_encuentro",
    "encuentro_dios", "post_encuentro",
    "nivel_1", "nivel_2", "nivel_3", "nivel_4", "nivel_5", "nivel_6",
]

ETAPAS_DISPLAY = {
    "visitante":        "Visitante",
    "grupo_encuentro":  "Grupo de Encuentro",
    "pre_encuentro":    "Pre-Encuentro",
    "encuentro_dios":   "Encuentro con Dios",
    "post_encuentro":   "Post-Encuentro",
    "nivel_1": "Escuela N1", "nivel_2": "Escuela N2",
    "nivel_3": "Escuela N3", "nivel_4": "Escuela N4",
    "nivel_5": "Escuela N5", "nivel_6": "Escuela N6",
}

CONCEPTOS_NIVEL = [
    {"mes": 1, "concepto": "Matrícula",   "monto": 20.0},
    {"mes": 1, "concepto": "Libro",       "monto": 15.0},
    {"mes": 1, "concepto": "Polo",        "monto": 20.0},
    {"mes": 2, "concepto": "Mensualidad", "monto": 20.0},
    {"mes": 3, "concepto": "Mensualidad", "monto": 20.0},
    {"mes": 4, "concepto": "Mensualidad", "monto": 20.0},
]


# ─── PERSONA (identidad universal) ───────────────────────────────────────────
class Persona(Base):
    __tablename__ = "personas"
    id             = Column(Integer, primary_key=True, index=True)
    dni            = Column(String(15), unique=True, nullable=False, index=True)
    nombre         = Column(String(100), nullable=False)
    apellido       = Column(String(100), default="")
    telefono       = Column(String(20), default="")
    email          = Column(String(120), default="")
    talla_polo     = Column(String(5), default="")
    fecha_nacimiento = Column(Date, nullable=True)
    fecha_registro = Column(DateTime, default=datetime.utcnow)
    activo         = Column(Boolean, default=True)

    roles         = relationship("PersonaRol", back_populates="persona", cascade="all, delete-orphan")
    usuario       = relationship("Usuario", back_populates="persona", uselist=False, cascade="all, delete-orphan")
    matriculas    = relationship("Matricula", back_populates="persona")
    proceso       = relationship("ProcesoEspiritual", back_populates="persona", uselist=False, cascade="all, delete-orphan")
    liderazgo_g12 = relationship("LiderG12", back_populates="persona", foreign_keys="LiderG12.persona_id")
    celulas       = relationship("Celula", back_populates="lider", foreign_keys="Celula.lider_id")

    @property
    def nombre_completo(self):
        return f"{self.nombre} {self.apellido}".strip()

    def tiene_rol(self, rol: str) -> bool:
        return any(r.rol == rol and r.activo for r in self.roles)

    def lista_roles(self) -> list:
        return [r.rol for r in self.roles if r.activo]

    def es_admin(self) -> bool:
        return self.tiene_rol("admin")


class PersonaRol(Base):
    __tablename__ = "persona_roles"
    __table_args__ = (UniqueConstraint("persona_id", "rol"),)
    id         = Column(Integer, primary_key=True)
    persona_id = Column(Integer, ForeignKey("personas.id"), nullable=False)
    rol        = Column(String(40), nullable=False)
    activo     = Column(Boolean, default=True)
    fecha_inicio = Column(DateTime, default=datetime.utcnow)
    persona    = relationship("Persona", back_populates="roles")


class Usuario(Base):
    __tablename__ = "usuarios"
    id               = Column(Integer, primary_key=True)
    persona_id       = Column(Integer, ForeignKey("personas.id"), unique=True, nullable=False)
    pin_hash         = Column(String(200), nullable=False)
    debe_cambiar_pin = Column(Boolean, default=True)
    ultimo_acceso    = Column(DateTime, nullable=True)
    persona          = relationship("Persona", back_populates="usuario")


# ─── PROCESO ESPIRITUAL ───────────────────────────────────────────────────────
class ProcesoEspiritual(Base):
    __tablename__ = "proceso_espiritual"
    id                 = Column(Integer, primary_key=True)
    persona_id         = Column(Integer, ForeignKey("personas.id"), unique=True)
    etapa              = Column(String(30), default="visitante")
    fecha_inicio_etapa = Column(DateTime, default=datetime.utcnow)
    notas              = Column(Text, default="")
    persona            = relationship("Persona", back_populates="proceso")


# ─── LIDERAZGO G12 ───────────────────────────────────────────────────────────
class LiderG12(Base):
    __tablename__ = "lideres_g12"
    id            = Column(Integer, primary_key=True)
    persona_id    = Column(Integer, ForeignKey("personas.id"), nullable=False)
    generacion    = Column(Integer, nullable=False)  # 1 o 2
    supervisor_id = Column(Integer, ForeignKey("personas.id"), nullable=True)
    fecha_inicio  = Column(DateTime, default=datetime.utcnow)
    activo        = Column(Boolean, default=True)
    persona       = relationship("Persona", foreign_keys=[persona_id], back_populates="liderazgo_g12")
    supervisor    = relationship("Persona", foreign_keys=[supervisor_id])


class Celula(Base):
    __tablename__ = "celulas"
    id          = Column(Integer, primary_key=True)
    nombre_casa = Column(String(60), nullable=False)
    emoji       = Column(String(10), default="🏠")
    lider_id    = Column(Integer, ForeignKey("personas.id"), nullable=True)
    activa      = Column(Boolean, default=True)
    lider       = relationship("Persona", foreign_keys=[lider_id], back_populates="celulas")
    reportes    = relationship("ReporteCelula", back_populates="celula")


class ReporteCelula(Base):
    __tablename__ = "reportes_celula"
    id              = Column(Integer, primary_key=True)
    celula_id       = Column(Integer, ForeignKey("celulas.id"), nullable=True)
    lider_dni       = Column(String(15), default="")
    lider_nombre    = Column(String(150), default="")
    tema            = Column(String(200), default="")
    fecha           = Column(Date, nullable=True)
    hora            = Column(String(10), default="")
    direccion       = Column(String(200), default="")
    tipo            = Column(String(20), default="Abierta")
    modalidad       = Column(String(20), default="presencial")
    es_nueva        = Column(Boolean, default=False)
    ofrenda         = Column(Float, default=0.0)
    observaciones   = Column(Text, default="")
    total_asistentes = Column(Integer, default=0)
    total_nuevos    = Column(Integer, default=0)
    created_at      = Column(DateTime, default=datetime.utcnow)
    celula          = relationship("Celula", back_populates="reportes")
    asistentes      = relationship("AsistenteCelula", back_populates="reporte", cascade="all, delete-orphan")


class AsistenteCelula(Base):
    __tablename__ = "asistentes_celula"
    id         = Column(Integer, primary_key=True)
    reporte_id = Column(Integer, ForeignKey("reportes_celula.id"))
    nombre     = Column(String(150), nullable=False)
    distrito   = Column(String(80), default="")
    es_nuevo   = Column(Boolean, default=False)
    edad       = Column(Integer, nullable=True)
    celular    = Column(String(20), default="")
    direccion  = Column(String(200), default="")
    reporte    = relationship("ReporteCelula", back_populates="asistentes")


# ─── GRUPOS DE ENCUENTRO ──────────────────────────────────────────────────────
class GrupoEncuentro(Base):
    __tablename__ = "grupos_encuentro"
    id              = Column(Integer, primary_key=True)
    nombre          = Column(String(100), nullable=False)
    encargado_id    = Column(Integer, ForeignKey("personas.id"), nullable=True)
    fecha_apertura  = Column(Date, nullable=True)
    fecha_encuentro = Column(Date, nullable=True)
    estado          = Column(String(20), default="activo")  # activo|en_proceso|completado|cerrado
    notas           = Column(Text, default="")
    inscripciones   = relationship("InscripcionEncuentro", back_populates="grupo", cascade="all, delete-orphan")


class InscripcionEncuentro(Base):
    __tablename__ = "inscripciones_encuentro"
    id                = Column(Integer, primary_key=True)
    persona_id        = Column(Integer, ForeignKey("personas.id"), nullable=False)
    grupo_id          = Column(Integer, ForeignKey("grupos_encuentro.id"), nullable=False)
    talla_polo        = Column(String(5), default="")
    estado            = Column(String(20), default="inscrito")  # inscrito|completado|retirado
    fecha_inscripcion = Column(DateTime, default=datetime.utcnow)
    persona           = relationship("Persona")
    grupo             = relationship("GrupoEncuentro", back_populates="inscripciones")
    pagos             = relationship("PagoEncuentro", back_populates="inscripcion", cascade="all, delete-orphan")


class PagoEncuentro(Base):
    __tablename__ = "pagos_encuentro"
    id                = Column(Integer, primary_key=True)
    inscripcion_id    = Column(Integer, ForeignKey("inscripciones_encuentro.id"))
    concepto          = Column(String(80), default="")
    monto             = Column(Float, nullable=False)
    fecha             = Column(DateTime, default=datetime.utcnow)
    metodo            = Column(String(20), default="efectivo")
    registrado_por_id = Column(Integer, ForeignKey("personas.id"), nullable=True)
    inscripcion       = relationship("InscripcionEncuentro", back_populates="pagos")


# ─── ESCUELA DE LIDERAZGO ─────────────────────────────────────────────────────
class Salon(Base):
    __tablename__ = "salones"
    id          = Column(Integer, primary_key=True, index=True)
    nivel       = Column(Integer, nullable=False)
    nombre      = Column(String(100), default="")
    maestro_id  = Column(Integer, ForeignKey("personas.id"), nullable=True)
    fecha_inicio = Column(String(20), default="")
    activo      = Column(Boolean, default=True)
    maestro     = relationship("Persona", foreign_keys=[maestro_id])
    matriculas  = relationship("Matricula", back_populates="salon")


class Matricula(Base):
    __tablename__ = "matriculas"
    id           = Column(Integer, primary_key=True)
    persona_id   = Column(Integer, ForeignKey("personas.id"), nullable=False)
    salon_id     = Column(Integer, ForeignKey("salones.id"), nullable=True)
    estado       = Column(String(20), default="activo")  # activo|por_graduarse|graduado|retirado
    fecha_ingreso = Column(DateTime, default=datetime.utcnow)
    persona      = relationship("Persona", back_populates="matriculas")
    salon        = relationship("Salon", back_populates="matriculas")
    conceptos    = relationship("ConceptoPago", back_populates="matricula", cascade="all, delete-orphan",
                                order_by="ConceptoPago.mes, ConceptoPago.id")
    abonos       = relationship("Abono", back_populates="matricula", cascade="all, delete-orphan")
    historial    = relationship("HistorialSalon", back_populates="matricula", cascade="all, delete-orphan")
    nota         = relationship("Nota", back_populates="matricula", uselist=False, cascade="all, delete-orphan")


class ConceptoPago(Base):
    __tablename__ = "conceptos_pago"
    id          = Column(Integer, primary_key=True)
    matricula_id = Column(Integer, ForeignKey("matriculas.id"))
    concepto    = Column(String(60))
    mes         = Column(Integer)
    monto       = Column(Float)
    matricula   = relationship("Matricula", back_populates="conceptos")


class Abono(Base):
    __tablename__ = "abonos"
    id          = Column(Integer, primary_key=True)
    matricula_id = Column(Integer, ForeignKey("matriculas.id"))
    monto       = Column(Float, nullable=False)
    metodo      = Column(String(20), default="efectivo")
    fecha       = Column(DateTime, default=datetime.utcnow)
    notas       = Column(String(200), default="")
    matricula   = relationship("Matricula", back_populates="abonos")


class HistorialSalon(Base):
    __tablename__ = "historial_salones"
    id           = Column(Integer, primary_key=True)
    matricula_id = Column(Integer, ForeignKey("matriculas.id"))
    salon_id     = Column(Integer, ForeignKey("salones.id"), nullable=True)
    fecha_ingreso = Column(DateTime, default=datetime.utcnow)
    fecha_salida = Column(DateTime, nullable=True)
    motivo       = Column(String(200), default="")
    matricula    = relationship("Matricula", back_populates="historial")
    salon        = relationship("Salon")


class Nota(Base):
    __tablename__ = "notas"
    id                 = Column(Integer, primary_key=True)
    matricula_id       = Column(Integer, ForeignKey("matriculas.id"), unique=True)
    asistencia         = Column(Float, nullable=True)
    uniforme           = Column(Float, nullable=True)
    devocional         = Column(Float, nullable=True)
    examen_oral        = Column(Float, nullable=True)
    control_libro      = Column(Float, nullable=True)
    examenes_mensuales = Column(Float, nullable=True)
    exposicion         = Column(Float, nullable=True)
    trabajo_final      = Column(Float, nullable=True)
    nota_final         = Column(Float, nullable=True)
    matricula          = relationship("Matricula", back_populates="nota")


# ─── OFRENDAS ─────────────────────────────────────────────────────────────────
class Culto(Base):
    __tablename__ = "cultos"
    id          = Column(Integer, primary_key=True)
    tipo        = Column(String(20), nullable=False)  # miercoles|sabado|domingo
    fecha       = Column(Date, nullable=False)
    descripcion = Column(String(200), default="")
    ofrendas    = relationship("Ofrenda", back_populates="culto", cascade="all, delete-orphan")
    asistencias  = relationship("AsistenciaCulto", back_populates="culto", cascade="all, delete-orphan")
    asignaciones = relationship("AsignacionCulto", back_populates="culto", cascade="all, delete-orphan")


class Ofrenda(Base):
    __tablename__ = "ofrendas"
    id                = Column(Integer, primary_key=True)
    culto_id          = Column(Integer, ForeignKey("cultos.id"))
    monto             = Column(Float, nullable=False)
    concepto          = Column(String(40), default="ofrenda")  # ofrenda|diezmo|siembra_mision
    registrado_por_id = Column(Integer, ForeignKey("personas.id"), nullable=True)
    hora              = Column(String(10), default="")
    culto             = relationship("Culto", back_populates="ofrendas")
    registrado_por    = relationship("Persona", foreign_keys=[registrado_por_id])


class SiembraCocina(Base):
    __tablename__ = "siembra_cocina"
    id                = Column(Integer, primary_key=True)
    fecha             = Column(Date, nullable=False)
    monto             = Column(Float, nullable=False)
    descripcion       = Column(String(200), default="")
    registrado_por_id = Column(Integer, ForeignKey("personas.id"), nullable=True)
    created_at        = Column(DateTime, default=datetime.utcnow)


# ─── REUNIÓN DE LÍDERES ───────────────────────────────────────────────────────
class ReunionLideres(Base):
    __tablename__ = "reuniones_lideres"
    id             = Column(Integer, primary_key=True)
    fecha          = Column(Date, nullable=False)
    descripcion    = Column(String(200), default="Reunión de líderes")
    creada_por_id  = Column(Integer, ForeignKey("personas.id"), nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow)
    asistencias    = relationship("AsistenciaLider", back_populates="reunion", cascade="all, delete-orphan")


class AsistenciaLider(Base):
    __tablename__ = "asistencias_lideres"
    __table_args__ = (UniqueConstraint("reunion_id", "persona_id"),)
    id          = Column(Integer, primary_key=True)
    reunion_id  = Column(Integer, ForeignKey("reuniones_lideres.id"))
    persona_id  = Column(Integer, ForeignKey("personas.id"))
    hora_entrada = Column(String(10), default="")
    reunion     = relationship("ReunionLideres", back_populates="asistencias")
    persona     = relationship("Persona")


class AsistenciaCulto(Base):
    __tablename__ = "asistencias_culto"
    __table_args__ = (UniqueConstraint("culto_id", "persona_id"),)
    id               = Column(Integer, primary_key=True)
    culto_id         = Column(Integer, ForeignKey("cultos.id"))
    persona_id       = Column(Integer, ForeignKey("personas.id"), nullable=True)
    nombre_visitante = Column(String(150), default="")
    tipo             = Column(String(20), default="miembro")  # miembro|visitante|nuevo
    created_at       = Column(DateTime, default=datetime.utcnow)
    culto            = relationship("Culto", back_populates="asistencias")
    persona          = relationship("Persona")


class Evento(Base):
    __tablename__ = "eventos"
    id             = Column(Integer, primary_key=True)
    titulo         = Column(String(200), nullable=False)
    descripcion    = Column(Text, default="")
    fecha_inicio   = Column(Date, nullable=False)
    fecha_fin      = Column(Date, nullable=True)
    hora_inicio    = Column(String(10), default="")
    hora_fin       = Column(String(10), default="")
    tipo           = Column(String(40), default="general")  # culto|reunion|retiro|conferencia|general
    lugar          = Column(String(200), default="")
    responsable_id = Column(Integer, ForeignKey("personas.id"), nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow)
    responsable    = relationship("Persona")


# ─── CHECKLISTS DE CULTO ──────────────────────────────────────────────────────
class AreaServicio(Base):
    __tablename__ = "areas_servicio"
    id      = Column(Integer, primary_key=True)
    nombre  = Column(String(100), nullable=False)
    icono   = Column(String(10), default="✅")
    orden   = Column(Integer, default=0)
    activo  = Column(Boolean, default=True)
    items_plantilla = relationship("ItemPlantillaArea", back_populates="area",
                                   cascade="all, delete-orphan", order_by="ItemPlantillaArea.orden")


class ItemPlantillaArea(Base):
    __tablename__ = "items_plantilla_area"
    id          = Column(Integer, primary_key=True)
    area_id     = Column(Integer, ForeignKey("areas_servicio.id"))
    descripcion = Column(String(300), nullable=False)
    orden       = Column(Integer, default=0)
    area        = relationship("AreaServicio", back_populates="items_plantilla")


class AsignacionCulto(Base):
    __tablename__ = "asignaciones_culto"
    __table_args__ = (UniqueConstraint("culto_id", "area_id"),)
    id              = Column(Integer, primary_key=True)
    culto_id        = Column(Integer, ForeignKey("cultos.id"))
    area_id         = Column(Integer, ForeignKey("areas_servicio.id"))
    responsable_id  = Column(Integer, ForeignKey("personas.id"), nullable=True)
    notas           = Column(Text, default="")
    estado          = Column(String(20), default="pendiente")  # pendiente|en_proceso|completado
    created_at      = Column(DateTime, default=datetime.utcnow)
    culto           = relationship("Culto", back_populates="asignaciones")
    area            = relationship("AreaServicio")
    responsable     = relationship("Persona", foreign_keys=[responsable_id])
    items           = relationship("ChecklistItemCulto", back_populates="asignacion",
                                   cascade="all, delete-orphan", order_by="ChecklistItemCulto.orden")


class ChecklistItemCulto(Base):
    __tablename__ = "checklist_items_culto"
    id                 = Column(Integer, primary_key=True)
    asignacion_id      = Column(Integer, ForeignKey("asignaciones_culto.id"))
    descripcion        = Column(String(300), nullable=False)
    orden              = Column(Integer, default=0)
    completado         = Column(Boolean, default=False)
    completado_por_id  = Column(Integer, ForeignKey("personas.id"), nullable=True)
    completado_at      = Column(DateTime, nullable=True)
    asignacion         = relationship("AsignacionCulto", back_populates="items")
    completado_por     = relationship("Persona", foreign_keys=[completado_por_id])


# ─── COLA: ENCUENTRO → ESCUELA ────────────────────────────────────────────────
class PendienteMatricula(Base):
    __tablename__ = "pendientes_matricula"
    id                      = Column(Integer, primary_key=True)
    persona_id              = Column(Integer, ForeignKey("personas.id"))
    inscripcion_encuentro_id = Column(Integer, ForeignKey("inscripciones_encuentro.id"), nullable=True)
    nivel_sugerido          = Column(String(50), default="Nivel 1")
    estado                  = Column(String(20), default="pendiente")  # pendiente|matriculado|descartado
    notas                   = Column(Text, default="")
    created_at              = Column(DateTime, default=datetime.utcnow)
    persona                 = relationship("Persona")
    inscripcion             = relationship("InscripcionEncuentro")


# ─── HELPERS ──────────────────────────────────────────────────────────────────
def saldo_matricula(m: Matricula) -> dict:
    deuda    = sum(c.monto for c in m.conceptos)
    abonado  = sum(a.monto for a in m.abonos)
    return {"deuda": deuda, "abonado": abonado, "pendiente": max(deuda - abonado, 0)}


def calcular_nota_final(n: Nota) -> float | None:
    campos = [n.asistencia, n.uniforme, n.devocional, n.examen_oral,
              n.control_libro, n.examenes_mensuales, n.exposicion, n.trabajo_final]
    vals = [v for v in campos if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


AREAS_SEED = [
    {"nombre": "Diaconado", "icono": "🙏", "orden": 1, "items": [
        "Llegar 45 min antes del culto",
        "Preparar fundas de ofrenda",
        "Recibir y acomodar a los asistentes",
        "Recoger la ofrenda durante el culto",
        "Registrar el monto de la ofrenda",
        "Entregar reporte de ofrenda al tesorero",
        "Verificar que el local quede ordenado al finalizar",
    ]},
    {"nombre": "Dirección del Culto", "icono": "🎙️", "orden": 2, "items": [
        "Preparar flujo / orden del culto",
        "Coordinar con el equipo de alabanza",
        "Confirmar al predicador y tema",
        "Revisar lista de anuncios",
        "Abrir en oración el culto",
        "Presentar al predicador",
        "Cerrar y enviar en paz",
    ]},
    {"nombre": "Multimedia / Sonido", "icono": "🎚️", "orden": 3, "items": [
        "Verificar proyector y pantalla",
        "Probar micrófonos y mezcla de audio",
        "Cargar letras de alabanzas en el sistema",
        "Preparar diapositivas de la prédica",
        "Configurar transmisión en vivo (si aplica)",
        "Grabar el culto",
        "Guardar y compartir la grabación",
    ]},
    {"nombre": "Cocina", "icono": "🍽️", "orden": 4, "items": [
        "Planificar el menú del refrigerio",
        "Comprar insumos necesarios",
        "Preparar el refrigerio antes del culto",
        "Servir el refrigerio al finalizar",
        "Limpiar y ordenar la cocina",
    ]},
    {"nombre": "Escuela de Niños", "icono": "🧸", "orden": 5, "items": [
        "Preparar la lección del día",
        "Decorar y acondicionar el salón de niños",
        "Registrar asistencia de niños",
        "Realizar la clase durante el culto",
        "Entregar niños a sus padres al finalizar",
        "Reportar asistencia y novedades",
    ]},
    {"nombre": "Equipo de Danza", "icono": "💃", "orden": 6, "items": [
        "Realizar ensayo previo al culto",
        "Coordinar vestuario con el equipo",
        "Coordinar con la dirección del culto el momento de la danza",
        "Ejecutar la presentación",
    ]},
    {"nombre": "Escuela de Líderes", "icono": "🎓", "orden": 7, "items": [
        "Preparar el material de la sesión",
        "Acondicionar el espacio de clase",
        "Registrar asistencia de alumnos",
        "Dictar la sesión",
        "Reportar novedades al coordinador",
    ]},
]


def init_db():
    Base.metadata.create_all(bind=engine)
    # Sembrar áreas de servicio si no existen
    db = SessionLocal()
    try:
        if db.query(AreaServicio).count() == 0:
            for a_data in AREAS_SEED:
                area = AreaServicio(nombre=a_data["nombre"], icono=a_data["icono"], orden=a_data["orden"])
                db.add(area)
                db.flush()
                for i, desc in enumerate(a_data["items"]):
                    db.add(ItemPlantillaArea(area_id=area.id, descripcion=desc, orden=i))
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
