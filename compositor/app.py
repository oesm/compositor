"""
Compositor de visuales — monta titular, logo y CTA sobre imágenes generadas por IA.
Marcas: Conecta, ERPymes, SM Group, Index, Tayta Mama Samay.

POST /componer  (JSON)
{
  "imagen_b64": "<base64 de la imagen base, PNG o JPG>",
  "marca": "erpymes | conecta | smgroup | index | taytamama",
  "formato": "feed | feed_vertical | historia | banner_linkedin | lista_beneficios | checklist_promo",
  "titular_1": "Cortes automáticos",
  "titular_2": "de morosos",             // opcional, va en color acento
  "subtitulo": "Vence la factura...",    // opcional
  "cta": "Agenda tu demo",               // opcional; historia/checklist_promo lo pintan como botón
  "items": [                             // solo para lista_beneficios y checklist_promo
    {"icono": "factura", "texto": "Facturación y cobranza"},
    {"icono": "caja", "texto": "Inventario y trazabilidad"}
  ],
  "mostrar_logo": true,                  // opcional, default true
  "mostrar_dominio": true                // opcional, default true
}
Iconos disponibles para "items[].icono": factura, caja, contabilidad, soporte, reportes, movil, personas, check (genérico).
Respuesta: PNG binario (image/png).
GET /salud → ok
"""
import base64, io, json, os, math
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from PIL import Image, ImageDraw, ImageFont

BASE = os.path.dirname(os.path.abspath(__file__))
BRANDS = json.load(open(os.path.join(BASE, "brands.json")))

DIMENSIONES = {
    "feed": (1080, 1080),
    "feed_vertical": (1080, 1350),
    "historia": (1080, 1920),
    "banner_linkedin": (1584, 396),
    "lista_beneficios": (1080, 1350),
    "checklist_promo": (1080, 1350),
}

app = FastAPI(title="Compositor de visuales")


class Item(BaseModel):
    icono: str = "check"
    texto: str = ""


class Pedido(BaseModel):
    imagen_b64: str = ""
    marca: str
    formato: str = "feed"
    titular_1: str = ""
    titular_2: str = ""
    subtitulo: str = ""
    cta: str = ""
    items: list[Item] = []
    mostrar_logo: bool = True
    mostrar_dominio: bool = True


def fuente(nombre, tam, peso=800):
    f = ImageFont.truetype(os.path.join(BASE, "assets", "fonts", nombre), tam)
    try:
        ejes = f.get_variation_axes()
        if len(ejes) == 2:      # Inter: [opsz, wght]
            f.set_variation_by_axes([28, peso])
        elif len(ejes) == 1:    # Montserrat / Playfair / Lora: [wght]
            f.set_variation_by_axes([min(peso, ejes[0]["maximum"])])
    except Exception:
        pass
    return f


def recortar_bordes_claros(img, umbral=238):
    """Recorta franjas casi blancas/uniformes pegadas a los bordes (letterbox de la IA)."""
    import numpy as np
    a = np.asarray(img.convert("L"), dtype=int)
    h, w = a.shape
    top = 0
    while top < h // 3 and a[top].mean() > umbral:
        top += 1
    bot = h
    while bot > h * 2 // 3 and a[bot - 1].mean() > umbral:
        bot -= 1
    izq = 0
    while izq < w // 3 and a[:, izq].mean() > umbral:
        izq += 1
    der = w
    while der > w * 2 // 3 and a[:, der - 1].mean() > umbral:
        der -= 1
    if (top, izq, bot, der) != (0, 0, h, w):
        img = img.crop((izq, top, der, bot))
    return img


def scrim_si_hace_falta(img, y0, y1, fuerza=150):
    """Si la zona del texto es clara, oscurece con un degradado para garantizar contraste."""
    import numpy as np
    zona = np.asarray(img.crop((0, y0, img.width, min(y1, img.height))).convert("L"))
    if zona.mean() < 135:
        return img
    alto = min(y1 + 60, img.height)
    grad = Image.new("L", (1, alto), 0)
    for i in range(alto):
        t = 1 - (i / alto)
        grad.putpixel((0, i), int(fuerza * (t ** 0.7)))
    capa = Image.new("RGBA", (img.width, alto), (12, 20, 26, 255))
    capa.putalpha(grad.resize((img.width, alto)))
    img.paste(capa, (0, 0), capa)
    return img


def cubrir(img, w, h):
    """Escala la imagen para cubrir w×h y recorta centrado."""
    sc = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * sc), round(img.height * sc)), Image.LANCZOS)
    x = (img.width - w) // 2
    y = (img.height - h) // 2
    return img.crop((x, y, x + w, y + h))


def texto_centrado(d, W, t, f, y, fill, sombra=True):
    b = d.textbbox((0, 0), t, font=f)
    x = (W - (b[2] - b[0])) // 2 - b[0]
    if sombra:
        d.text((x + 2, y + 2), t, font=f, fill=(0, 0, 0, 150))
    d.text((x, y), t, font=f, fill=tuple(fill))


def ajustar_titulo(d, texto, nombre_fuente, tam_ini, max_w, peso=800):
    """Reduce el tamaño hasta que el texto quepa en max_w."""
    tam = tam_ini
    while tam > 30:
        f = fuente(nombre_fuente, tam, peso)
        b = d.textbbox((0, 0), texto, font=f)
        if b[2] - b[0] <= max_w:
            return f, tam
        tam -= 4
    return fuente(nombre_fuente, tam, peso), tam


def envolver_texto(d, texto, f, max_w):
    """Parte el texto en líneas que quepan dentro de max_w (word-wrap real, no corta palabras)."""
    palabras = texto.split()
    if not palabras:
        return []
    lineas, actual = [], ""
    for palabra in palabras:
        prueba = (actual + " " + palabra).strip()
        b = d.textbbox((0, 0), prueba, font=f)
        if b[2] - b[0] <= max_w or not actual:
            actual = prueba
        else:
            lineas.append(actual)
            actual = palabra
    if actual:
        lineas.append(actual)
    return lineas


def ajustar_y_envolver(d, texto, nombre_fuente, tam_ini, max_w, max_lineas=3, peso=500, tam_min=22):
    """
    Para subtítulos: intenta con tam_ini; si el texto envuelto ocupa más de max_lineas,
    reduce el tamaño de fuente (igual que ajustar_titulo) hasta que quepa en max_lineas,
    sin bajar de tam_min. Devuelve (fuente, lista_de_lineas).
    """
    tam = tam_ini
    while tam > tam_min:
        f = fuente(nombre_fuente, tam, peso)
        lineas = envolver_texto(d, texto, f, max_w)
        if len(lineas) <= max_lineas:
            return f, lineas
        tam -= 2
    f = fuente(nombre_fuente, tam_min, peso)
    return f, envolver_texto(d, texto, f, max_w)


def texto_multilinea_centrado(d, W, lineas, f, y, fill, interlineado=1.28, sombra=True):
    """Dibuja cada línea centrada, apilada verticalmente. Devuelve la y final (después del bloque)."""
    alto_linea = int(f.size * interlineado)
    for i, linea in enumerate(lineas):
        texto_centrado(d, W, linea, f, y + i * alto_linea, fill, sombra)
    return y + len(lineas) * alto_linea


def texto_izquierda(d, x, t, f, y, fill):
    """Dibuja una sola línea alineada a la izquierda en (x, y), sin sombra."""
    d.text((x, y), t, font=f, fill=tuple(fill))


def texto_multilinea_izquierda(d, x, lineas, f, y, fill, interlineado=1.28):
    """Dibuja varias líneas alineadas a la izquierda, apiladas. Devuelve la y final."""
    alto_linea = int(f.size * interlineado)
    for i, linea in enumerate(lineas):
        texto_izquierda(d, x, linea, f, y + i * alto_linea, fill)
    return y + len(lineas) * alto_linea


def _linea(d, puntos, color, grosor):
    d.line(puntos, fill=color, width=grosor, joint="curve")
    r = grosor // 2
    for (x, y) in (puntos[0], puntos[-1]):
        d.ellipse([x - r, y - r, x + r, y + r], fill=color)


def icono_factura(d, x, y, s, c1, c2):
    g = max(2, int(s * 0.045))
    d.rounded_rectangle([x + s * 0.12, y, x + s * 0.72, y + s], radius=s * 0.06, outline=c1, width=g)
    for i in range(3):
        yy = y + s * 0.24 + i * s * 0.2
        _linea(d, [(x + s * 0.24, yy), (x + s * 0.6, yy)], c1, g)
    cx, cy, r = x + s * 0.78, y + s * 0.78, s * 0.22
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c2)
    f = ImageFont.load_default()
    d.text((cx, cy), "$", fill=(255, 255, 255), anchor="mm")


def icono_caja(d, x, y, s, c1, c2):
    g = max(2, int(s * 0.045))
    top = y + s * 0.28
    d.line([(x + s * 0.06, top), (x + s * 0.5, y), (x + s * 0.94, top)], fill=c1, width=g, joint="curve")
    d.rounded_rectangle([x + s * 0.06, top, x + s * 0.94, y + s], radius=s * 0.04, outline=c1, width=g)
    _linea(d, [(x + s * 0.5, top), (x + s * 0.5, y + s * 0.9)], c2, g)


def icono_contabilidad(d, x, y, s, c1, c2):
    g = max(2, int(s * 0.045))
    d.rounded_rectangle([x + s * 0.1, y, x + s * 0.9, y + s], radius=s * 0.08, outline=c1, width=g)
    for fila in range(3):
        for col in range(3):
            cx0 = x + s * 0.22 + col * s * 0.2
            cy0 = y + s * 0.24 + fila * s * 0.22
            col_ = c2 if (fila == 2 and col == 2) else c1
            d.rounded_rectangle([cx0, cy0, cx0 + s * 0.12, cy0 + s * 0.12], radius=s * 0.02, fill=col_)


def icono_soporte(d, x, y, s, c1, c2):
    g = max(2, int(s * 0.06))
    cx, cy, r = x + s * 0.5, y + s * 0.46, s * 0.4
    d.arc([cx - r, cy - r, cx + r, cy + r], 200, 340, fill=c1, width=g)
    d.rounded_rectangle([x + s * 0.08, y + s * 0.4, x + s * 0.24, y + s * 0.72], radius=s * 0.06, fill=c1)
    d.rounded_rectangle([x + s * 0.76, y + s * 0.4, x + s * 0.92, y + s * 0.72], radius=s * 0.06, fill=c1)
    d.arc([x + s * 0.3, y + s * 0.66, x + s * 0.7, y + s * 1.0], 20, 160, fill=c2, width=g)


def icono_reportes(d, x, y, s, c1, c2):
    g = max(2, int(s * 0.09))
    base_y = y + s * 0.92
    barras = [(0.18, 0.42, c1), (0.44, 0.66, c2), (0.70, 0.88, c1)]
    for (bx, alto, color) in barras:
        d.line([(x + s * bx, base_y), (x + s * bx, y + s - s * alto)], fill=color, width=g)


def icono_movil(d, x, y, s, c1, c2):
    g = max(2, int(s * 0.05))
    d.rounded_rectangle([x + s * 0.28, y, x + s * 0.72, y + s], radius=s * 0.1, outline=c1, width=g)
    cx, cy, r = x + s * 0.5, y + s * 0.9, s * 0.045
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c2)


def icono_personas(d, x, y, s, c1, c2):
    for dx, color, esc in [(0.28, c1, 1.0), (0.62, c2, 0.85)]:
        cx = x + s * dx
        r = s * 0.16 * esc
        cy = y + s * 0.22
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=max(2, int(s * 0.045)))
        d.arc([cx - r * 1.7, cy + r * 0.7, cx + r * 1.7, cy + r * 3.6], 200, 340, fill=color, width=max(2, int(s * 0.045)))


def icono_check(d, x, y, s, c1, c2):
    g = max(3, int(s * 0.1))
    cx, cy, r = x + s * 0.5, y + s * 0.5, s * 0.46
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=c2, width=g)
    _linea(d, [(cx - r * 0.45, cy), (cx - r * 0.1, cy + r * 0.35), (cx + r * 0.5, cy - r * 0.4)], c2, g)


ICONOS = {
    "factura": icono_factura,
    "caja": icono_caja,
    "contabilidad": icono_contabilidad,
    "soporte": icono_soporte,
    "reportes": icono_reportes,
    "movil": icono_movil,
    "personas": icono_personas,
    "check": icono_check,
}

# color_titulo en brands.json está pensado para texto sobre foto oscura (suele ser blanco).
# Para las plantillas de fondo BLANCO (lista_beneficios, checklist_promo) hace falta un
# color oscuro real por marca; blanco sobre blanco sería invisible.
OSCURO_POR_MARCA = {
    "conecta": (46, 95, 146),      # azul #2E5F92
    "erpymes": (31, 78, 110),      # azul #1F4E6E
    "smgroup": (10, 95, 168),      # azul #0A5FA8
    "index": (33, 33, 33),         # carbón #212121
    "taytamama": (53, 32, 15),     # café #35200F
}


def dibujar_icono(d, nombre, x, y, s, c1, c2):
    fn = ICONOS.get(nombre, icono_check)
    fn(d, x, y, s, c1, c2)


@app.get("/salud")
def salud():
    return {"ok": True}


@app.post("/componer")
def componer(p: Pedido):
    marca = BRANDS.get(p.marca.lower().strip())
    if not marca:
        raise HTTPException(400, f"Marca desconocida: {p.marca}. Opciones: {list(BRANDS)}")
    if p.formato not in DIMENSIONES:
        raise HTTPException(400, f"Formato desconocido: {p.formato}. Opciones: {list(DIMENSIONES)}")

    W, H = DIMENSIONES[p.formato]
    ft, fx = marca["fuente_titulo"], marca["fuente_texto"]
    C_TIT, C_ACC, C_SUB = marca["color_titulo"], marca["color_acento"], marca["color_sub"]
    margen = 56 if p.formato != "banner_linkedin" else 90

    # --- Formatos "template" (sin foto de IA como fondo, layout tipo plantilla) ---
    if p.formato in ("lista_beneficios", "checklist_promo"):
        C_OSC = OSCURO_POR_MARCA.get(p.marca.lower().strip(), (26, 30, 36))
        img = Image.new("RGB", (W, H), (255, 255, 255))
        d = ImageDraw.Draw(img, "RGBA")

        # logo arriba
        y = margen
        if p.mostrar_logo:
            ruta = os.path.join(BASE, "assets", "logos", marca["logo_oscuro"])
            logo = Image.open(ruta).convert("RGBA")
            lw = 260
            logo = logo.resize((lw, round(logo.height * lw / logo.width)), Image.LANCZOS)
            img.paste(logo, (margen, y), logo)
            d = ImageDraw.Draw(img, "RGBA")
            y += logo.height + 36

        if p.formato == "lista_beneficios":
            panel_w = int(W * 0.52)
            if p.titular_1:
                f1, lineas1 = ajustar_y_envolver(d, p.titular_1, ft, 62, panel_w - margen, max_lineas=3, peso=800)
                y = texto_multilinea_izquierda(d, margen, lineas1, f1, y, C_OSC, interlineado=1.15) + 24
            filas = min(len(p.items), 8)
            alto_disp = H - y - margen
            alto_fila = min(96, alto_disp // max(filas, 1))
            ic_s = int(alto_fila * 0.62)
            for it in p.items[:filas]:
                dibujar_icono(d, it.icono, margen, y + (alto_fila - ic_s) // 2, ic_s, tuple(C_OSC), tuple(C_ACC))
                f_it = fuente(fx, 34, 500)
                _, lineas_it = ajustar_y_envolver(d, it.texto, fx, 34, panel_w - margen - ic_s - 28, max_lineas=1, peso=500)
                texto_izquierda(d, margen + ic_s + 28, lineas_it[0] if lineas_it else it.texto, f_it,
                                 y + alto_fila // 2 - 20, C_OSC)
                y += alto_fila
            # foto de IA a la derecha, si vino
            if p.imagen_b64:
                try:
                    foto = Image.open(io.BytesIO(base64.b64decode(p.imagen_b64))).convert("RGB")
                    foto = recortar_bordes_claros(foto)
                    foto = cubrir(foto, W - panel_w, H)
                    img.paste(foto, (panel_w, 0))
                except Exception:
                    pass
            if p.mostrar_dominio and marca["dominio"]:
                barra_h = 90
                d = ImageDraw.Draw(img, "RGBA")
                d.rectangle([0, H - barra_h, W, H], fill=tuple(C_OSC))
                fd = fuente(fx, 32, 600)
                texto_centrado(d, W, marca["dominio"], fd, H - barra_h // 2 - 18, (255, 255, 255), sombra=False)

        else:  # checklist_promo
            if p.titular_1:
                f1, lineas1 = ajustar_y_envolver(d, p.titular_1, ft, 66, W - 2 * margen, max_lineas=2, peso=800)
                y = texto_multilinea_centrado(d, W, lineas1, f1, y, C_OSC, interlineado=1.12, sombra=False) + 6
            if p.titular_2:
                f2, lineas2 = ajustar_y_envolver(d, p.titular_2, ft, 44, W - 2 * margen, max_lineas=2, peso=700)
                y = texto_multilinea_centrado(d, W, lineas2, f2, y, C_ACC, interlineado=1.15, sombra=False) + 32
            else:
                y += 24
            filas = min(len(p.items), 6)
            for it in p.items[:filas]:
                ic_s = 52
                dibujar_icono(d, "check", margen, y, ic_s, tuple(C_ACC), tuple(C_ACC))
                f_it = fuente(fx, 36, 700)
                texto_izquierda(d, margen + ic_s + 24, it.texto, f_it, y + 6, C_OSC)
                y += ic_s + 30
            y += 20
            if p.cta:
                fb = fuente(ft, 46, 800)
                b = d.textbbox((0, 0), p.cta, font=fb)
                bw = (b[2] - b[0]) + 140
                x0 = (W - bw) // 2
                by0 = min(y, H - margen - 150)
                d.rounded_rectangle([x0, by0, x0 + bw, by0 + 96], radius=48, fill=tuple(C_ACC))
                texto_centrado(d, W, p.cta, fb, by0 + 24, (255, 255, 255), sombra=False)
            if p.mostrar_dominio and marca["dominio"]:
                texto_centrado(d, W, marca["dominio"], fuente(fx, 32, 600), H - margen - 20, C_OSC, sombra=False)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(content=buf.getvalue(), media_type="image/png")

    # --- Formatos "foto de IA + texto encima" (comportamiento original) ---
    try:
        base = Image.open(io.BytesIO(base64.b64decode(p.imagen_b64))).convert("RGB")
    except Exception as e:
        raise HTTPException(400, f"imagen_b64 inválida: {e}")

    base = recortar_bordes_claros(base)
    img = cubrir(base, W, H)
    # garantizar contraste del titular (por si la IA dejó la zona clara)
    if p.formato in ("feed", "feed_vertical") and (p.titular_1 or p.subtitulo):
        img = scrim_si_hace_falta(img, 0, 380)
    elif p.formato == "historia" and (p.titular_1 or p.subtitulo):
        img = scrim_si_hace_falta(img, 240, 620)
    d = ImageDraw.Draw(img, "RGBA")

    if p.formato == "banner_linkedin":
        # texto a la izquierda (lado limpio), una o dos líneas
        f1, _ = ajustar_titulo(d, p.titular_1, ft, 64, int(W * 0.62))
        d.text((margen, 105 if p.titular_2 else 140), p.titular_1, font=f1, fill=tuple(C_TIT))
        if p.titular_2:
            f2, _ = ajustar_titulo(d, p.titular_2, ft, 64, int(W * 0.62))
            d.text((margen, 190), p.titular_2, font=f2, fill=tuple(C_ACC))
        if p.mostrar_dominio and marca["dominio"]:
            d.text((margen + 2, 292), marca["dominio"], font=fuente(fx, 30, 600), fill=tuple(C_SUB))

    elif p.formato == "historia":
        # zonas seguras: 250px arriba, 420px abajo
        y = 300
        if p.titular_1:
            f1, tam = ajustar_titulo(d, p.titular_1, ft, 78, W - 2 * margen)
            texto_centrado(d, W, p.titular_1, f1, y, C_TIT); y += int(tam * 1.28)
        if p.titular_2:
            f2, tam = ajustar_titulo(d, p.titular_2, ft, 78, W - 2 * margen)
            texto_centrado(d, W, p.titular_2, f2, y, C_ACC); y += int(tam * 1.35)
        if p.subtitulo:
            f_sub, lineas = ajustar_y_envolver(d, p.subtitulo, fx, 38, W - 2 * margen, max_lineas=3, peso=500)
            texto_multilinea_centrado(d, W, lineas, f_sub, y + 8, C_SUB)
        if p.cta:
            fb = fuente(ft, 48, 800)
            b = d.textbbox((0, 0), p.cta, font=fb)
            bw = (b[2] - b[0]) + 120
            x0 = (W - bw) // 2
            d.rounded_rectangle([x0, 1330, x0 + bw, 1428], radius=49, fill=tuple(C_ACC))
            texto_centrado(d, W, p.cta, fb, 1352, (255, 255, 255), sombra=False)
        if p.mostrar_dominio and marca["dominio"]:
            texto_centrado(d, W, marca["dominio"], fuente(fx, 34, 600), 1448, C_SUB)

    else:  # feed y feed_vertical
        y = 64
        if p.titular_1:
            f1, tam = ajustar_titulo(d, p.titular_1, ft, 82, W - 2 * margen)
            texto_centrado(d, W, p.titular_1, f1, y, C_TIT); y += int(tam * 1.28)
        if p.titular_2:
            f2, tam = ajustar_titulo(d, p.titular_2, ft, 82, W - 2 * margen)
            texto_centrado(d, W, p.titular_2, f2, y, C_ACC); y += int(tam * 1.38)
        if p.subtitulo:
            f_sub, lineas = ajustar_y_envolver(d, p.subtitulo, fx, 36, W - 2 * margen, max_lineas=3, peso=500)
            texto_multilinea_centrado(d, W, lineas, f_sub, y, C_SUB)
        if p.mostrar_logo:
            ruta = os.path.join(BASE, "assets", "logos", marca["logo_oscuro"])
            logo = Image.open(ruta).convert("RGBA")
            lw = 230
            logo = logo.resize((lw, round(logo.height * lw / logo.width)), Image.LANCZOS)
            img.paste(logo, (margen, H - margen - logo.height), logo)
            d = ImageDraw.Draw(img, "RGBA")
        if p.mostrar_dominio and marca["dominio"]:
            fd = fuente(ft, 40, 700)
            b = d.textbbox((0, 0), marca["dominio"], font=fd)
            d.text((W - margen - (b[2] - b[0]), H - margen - (b[3] - b[1]) - b[1]),
                   marca["dominio"], font=fd, fill=tuple(C_ACC))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")
