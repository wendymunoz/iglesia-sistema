"""
Ejecutar una vez para crear el primer administrador del sistema.
Uso: python setup_admin.py
"""
from database import init_db, SessionLocal, Persona, PersonaRol, Usuario, ProcesoEspiritual
from auth import hashear_pin, pin_inicial

def main():
    print("═══════════════════════════════════════")
    print("  Setup — Sistema Iglesia")
    print("  Creación del primer administrador")
    print("═══════════════════════════════════════\n")

    init_db()
    db = SessionLocal()

    dni = input("DNI del administrador: ").strip()
    if not dni:
        print("❌ DNI requerido."); return

    existe = db.query(Persona).filter(Persona.dni == dni).first()
    if existe:
        print(f"⚠️  Ya existe una persona con DNI {dni}: {existe.nombre_completo}")
        if not existe.tiene_rol("admin"):
            existe.roles.append(PersonaRol(rol="admin"))
            if not existe.usuario:
                db.add(Usuario(persona_id=existe.id, pin_hash=hashear_pin(pin_inicial(dni)), debe_cambiar_pin=True))
            db.commit()
            print("✅ Rol admin asignado.")
        else:
            print("✅ Ya tiene rol admin.")
        db.close(); return

    nombre = input("Nombre(s): ").strip()
    apellido = input("Apellido(s): ").strip()

    p = Persona(dni=dni, nombre=nombre, apellido=apellido)
    db.add(p); db.flush()
    db.add(PersonaRol(persona_id=p.id, rol="admin"))
    db.add(ProcesoEspiritual(persona_id=p.id, etapa="visitante"))
    pin = pin_inicial(dni)
    db.add(Usuario(persona_id=p.id, pin_hash=hashear_pin(pin), debe_cambiar_pin=True))
    db.commit()

    print(f"\n✅ Administrador creado:")
    print(f"   Nombre: {p.nombre_completo}")
    print(f"   DNI:    {dni}")
    print(f"   PIN inicial: {pin} (primeros 4 dígitos del DNI)")
    print(f"\n⚠️  Cambia el PIN al primer login en: http://localhost:8000/login")
    db.close()

if __name__ == "__main__":
    main()
