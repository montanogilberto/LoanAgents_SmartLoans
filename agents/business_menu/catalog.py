"""POS modules the business_menu agent may suggest. Ids must match the
frontend signup wizard (POSVending CreateAccount.tsx ALL_MODULES); the
route drops any other id the model returns."""

POS_MODULES = (
    {"id": "pos_cart", "label": "Carrito / Punto de Venta", "use": "Vender y cobrar productos o servicios."},
    {"id": "cash_register", "label": "Caja Registradora", "use": "Abrir y cerrar caja, manejar efectivo."},
    {"id": "inventory_products", "label": "Productos e Inventario", "use": "Catálogo de productos con existencias."},
    {"id": "receipts_printing", "label": "Recibos e Impresión", "use": "Imprimir o enviar tickets."},
    {"id": "accounting_ledger", "label": "Contabilidad y Finanzas", "use": "Ingresos, egresos y contabilidad."},
    {"id": "clients", "label": "Gestión de Clientes", "use": "Registro de clientes, pedidos por cliente, seguimiento."},
    {"id": "rewards", "label": "Puntos de Recompensa", "use": "Puntos, promociones y lealtad de clientes."},
    {"id": "pushNotifications", "label": "Notificaciones Push", "use": "Avisar al cliente (pedido listo, promociones)."},
)

MODULE_IDS = tuple(m["id"] for m in POS_MODULES)
