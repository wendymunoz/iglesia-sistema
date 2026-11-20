"""
Script para insertar datos de ejemplo en la base de datos.
Uso: python seed_data.py
"""
import os, sys
from datetime import date, datetime, timedelta
from dotenv import load_dotenv
load_dotenv()

from database import (
    SessionLocal, init_db,
    Persona, PersonaRol, Usuario, ProcesoEspiritual,
    Celula, ReporteCelula,
    Salon, Matricula, ConceptoPago, HistorialSalon,
    Culto, Ofrenda,
    LiderG12,
    GrupoEncuentro, InscripcionEncuentro,
    CONCEPTOS_NIVEL,
)
from auth import hashear_pin, pin_inicial

PERSONAS = [
    {"dni":"10000001","nombre":"María","apellido":"Torres Quispe",    "fn":date(1990,3,15),"tel":"987654321"},
    {"dni":"10000002","nombre":"Juan",  "apellido":"Flores Mamani",   "fn":date(1985,7,22),"tel":"987654322"},
    {"dni":"10000003","nombre":"Rosa",  "apellido":"Chávez López",    "fn":date(1992,11,5),"tel":"987654323"},
    {"dni":"10000004","nombre":"Pedro", "apellido":"Gutiérrez Ramos", "fn":date(1988,1,30),"tel":"987654324"},
    {"dni":"10000005","nombre":"Ana",   "apellido":"Mendoza Silva",   "fn":date(1995,6,18),"tel":"987654325"},
    {"dni":"10000006","nombre":"Luis",  "apellido":"Vargas Huanca",   "fn":date(1983,9,10),"tel":"987654326"},
    {"dni":"10000007","nombre":"Carmen","apellido":"Soto Ccasa",      "fn":date(1998,4,25),"tel":"987654327"},
    {"dni":"10000008","nombre":"Carlos","apellido":"Ríos Paucar",     "fn":date(1991,12,8),"tel":"987654328"},
    {"dni":"10000009","nombre":"Elena", "apellido":"Paredes Condori", "fn":date(1987,2,14),"tel":"987654329"},
    {"dni":"10000010","nombre":"Diego", "apellido":"Cabrera Apaza",   "fn":date(1994,8,20),"tel":"987654330"},
]

def run():
    init_db()
    db = SessionLocal()
    try:
        # Verificar si ya hay datos
        if db.query(Persona).filter(Persona.dni == "10000001").first():
            print("⚠️  Datos de ejemplo ya existen. Nada que hacer.")
            return

        print("Insertando personas...")
        personas = []
        for i, d in enumerate(PERSONAS):
            p = Persona(
                dni=d["dni"], nombre=d["nombre"], apellido=d["apellido"],
                fecha_nacimiento=d["fn"], telefono=d["tel"], activo=True,
            )
            db.add(p); db.flush()
            etapa = "nivel_1" if i < 4 else ("post_encuentro" if i < 7 else "visitante")
            db.add(ProcesoEspiritual(persona_id=p.id, etapa=etapa))
            db.add(Usuario(persona_id=p.id, pin_hash=hashear_pin(pin_inicial(d["dni"])), debe_cambiar_pin=False))
            personas.append(p)

        # Líderes G12
        print("Configurando red G12...")
        db.add(PersonaRol(persona_id=personas[0].id, rol="lider_g1"))
        db.add(PersonaRol(persona_id=personas[1].id, rol="lider_g1"))
        db.add(PersonaRol(persona_id=personas[2].id, rol="lider_g2"))
        db.add(LiderG12(persona_id=personas[0].id, generacion=1, activo=True))
        db.add(LiderG12(persona_id=personas[1].id, generacion=1, activo=True))
        db.add(LiderG12(persona_id=personas[2].id, generacion=2, supervisor_id=personas[0].id, activo=True))
        db.flush()

        # Células
        print("Creando células...")
        c1 = Celula(nombre="Célula Esperanza", lider_id=personas[0].id, direccion="Jr. Los Pinos 123", dia_reunion="Martes", hora_reunion="19:00", activa=True)
        c2 = Celula(nombre="Célula Fe", lider_id=personas[1].id, direccion="Av. Los Olivos 456", dia_reunion="Jueves", hora_reunion="19:30", activa=True)
        db.add(c1); db.add(c2); db.flush()

        # Salones de escuela
        print("Verificando salones...")
        salon1 = db.query(Salon).filter(Salon.nivel == 1).first()
        salon2 = db.query(Salon).filter(Salon.nivel == 2).first()

        if salon1:
            print("Matriculando estudiantes en Nivel 1...")
            for p in personas[:4]:
                ya = db.query(Matricula).filter(Matricula.persona_id == p.id, Matricula.salon_id == salon1.id).first()
                if not ya:
                    m = Matricula(persona_id=p.id, salon_id=salon1.id, estado="activo", fecha_ingreso=date.today())
                    db.add(m); db.flush()
                    for c in CONCEPTOS_NIVEL:
                        db.add(ConceptoPago(matricula_id=m.id, **c))
                    db.add(HistorialSalon(matricula_id=m.id, salon_id=salon1.id, motivo="Matrícula inicial"))

        if salon2:
            print("Matriculando estudiantes en Nivel 2...")
            for p in personas[4:7]:
                ya = db.query(Matricula).filter(Matricula.persona_id == p.id, Matricula.salon_id == salon2.id).first()
                if not ya:
                    m = Matricula(persona_id=p.id, salon_id=salon2.id, estado="activo", fecha_ingreso=date.today())
                    db.add(m); db.flush()
                    for c in CONCEPTOS_NIVEL:
                        db.add(ConceptoPago(matricula_id=m.id, **c))
                    db.add(HistorialSalon(matricula_id=m.id, salon_id=salon2.id, motivo="Matrícula inicial"))

        # Cultos con ofrendas
        print("Creando cultos y ofrendas...")
        hoy = date.today()
        cultos_data = [
            {"tipo":"domingo",   "fecha": hoy - timedelta(days=14), "monto": 850.0},
            {"tipo":"miercoles", "fecha": hoy - timedelta(days=11), "monto": 320.0},
            {"tipo":"domingo",   "fecha": hoy - timedelta(days=7),  "monto": 920.0},
            {"tipo":"miercoles", "fecha": hoy - timedelta(days=4),  "monto": 280.0},
            {"tipo":"domingo",   "fecha": hoy,                       "monto": 1050.0},
        ]
        for cd in cultos_data:
            culto = Culto(tipo=cd["tipo"], fecha=cd["fecha"])
            db.add(culto); db.flush()
            db.add(Ofrenda(culto_id=culto.id, monto=cd["monto"], concepto="Ofrenda general", registrado_por_id=personas[0].id))

        # Grupo de encuentro
        print("Creando grupo de encuentro...")
        grupo = GrupoEncuentro(
            nombre="Encuentro Nov 2026",
            fecha_apertura=hoy + timedelta(days=10),
            fecha_encuentro=hoy + timedelta(days=12),
            encargado_id=personas[0].id,
            estado="activo",
        )
        db.add(grupo); db.flush()
        for p in personas[7:]:
            db.add(InscripcionEncuentro(grupo_id=grupo.id, persona_id=p.id, estado="inscrito"))

        db.commit()
        print("\n✅ Datos de ejemplo insertados correctamente:")
        print(f"   - {len(PERSONAS)} personas")
        print(f"   - 2 células")
        print(f"   - 3 líderes G12")
        print(f"   - Matrículas en salones")
        print(f"   - {len(cultos_data)} cultos con ofrendas")
        print(f"   - 1 grupo de encuentro con 3 inscritos")

    except Exception as e:
        db.rollback()
        print(f"❌ Error: {e}")
        import traceback; traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    run()
