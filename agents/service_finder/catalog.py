"""The GMO services a visitor can sign up for — the facts the service finder
reasons over. Kept here, not in the prompt (prompts stay business-agnostic);
the route sends it in every request. Ids must match the frontend's
ServiceFinder (POSVending src/pages/authentication/ServiceFinder/)."""

SERVICES = (
    {
        "id": "pos",
        "name": "POS (Punto de venta)",
        "summary": "Sistema para negocios: ventas, cobro, caja, inventario, recibos, clientes y puntos de recompensa.",
        "forWho": "Dueños o empleados de un negocio con local (tienda, lavandería, restaurante, servicios).",
    },
    {
        "id": "smartloans",
        "name": "SmartLoans (P2P)",
        "summary": "Préstamos entre personas: quien necesita dinero lo pide y quien tiene dinero lo presta; el pago va directo entre ellos por SPEI, con contrato y seguimiento.",
        "forWho": "Personas que necesitan un préstamo o que quieren prestar su dinero.",
    },
    {
        "id": "factory",
        "name": "FactoryAI",
        "summary": "Genera software con IA: la persona describe su idea o proceso y se genera el código de su app o sistema a la medida.",
        "forWho": "Emprendedores, empresas o desarrolladores que quieren una app, sistema o automatización propia.",
    },
    {
        "id": "arcade",
        "name": "Juegos Online",
        "summary": "Mini-juegos en línea con fichas virtuales. Las fichas no se canjean por dinero ni premios.",
        "forWho": "Personas que buscan entretenimiento.",
    },
)

SERVICE_IDS = tuple(s["id"] for s in SERVICES)
