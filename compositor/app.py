"""
Compositor de visuales — monta titular, logo y CTA sobre imágenes generadas por IA.
Marcas: Conecta, ERPymes, SM Group, Index, Tayta Mama Samay.

POST /componer  (JSON)
{
  "imagen_b64": "<base64 de la imagen base, PNG o JPG>",
  "marca": "erpymes | conecta | smgroup | index | taytamama",
  "formato": "feed | feed_vertical | historia | banner_linkedin",
  "titular_1": "Cortes automáticos",
  "titular_2": "de morosos",             // opcional, va en color acento
  "subtitulo": "Vence la factura...",    // opcional
  "cta": "Agenda tu demo",               // opcional; historia lo pinta como botón
  "mostrar_logo": true,                  // opcional, default true
  "mostrar_dominio": true                // opcional, default true
}
Respuesta: PNG binario (image/png).
GET /salud → ok
"""
import base64, io, json, os
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
}

app = FastAPI(title="Compositor de visuales")


class Pedido(BaseModel):
    imagen_b64: str
    marca: str
    formato: str = "feed"
    titular_1: str = ""
    titular_2: str = ""
    subtitulo: str = ""
    cta: str = ""
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

    try:
        base = Image.open(io.BytesIO(base64.b64decode(p.imagen_b64))).convert("RGB")
    except Exception as e:
        raise HTTPException(400, f"imagen_b64 inválida: {e}")

    W, H = DIMENSIONES[p.formato]
    base = recortar_bordes_claros(base)
    img = cubrir(base, W, H)
    # garantizar contraste del titular (por si la IA dejó la zona clara)
    if p.formato in ("feed", "feed_vertical") and (p.titular_1 or p.subtitulo):
        img = scrim_si_hace_falta(img, 0, 380)
    elif p.formato == "historia" and (p.titular_1 or p.subtitulo):
        img = scrim_si_hace_falta(img, 240, 620)
    d = ImageDraw.Draw(img, "RGBA")

    ft, fx = marca["fuente_titulo"], marca["fuente_texto"]
    C_TIT, C_ACC, C_SUB = marca["color_titulo"], marca["color_acento"], marca["color_sub"]
    margen = 56 if p.formato != "banner_linkedin" else 90

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
            texto_centrado(d, W, p.subtitulo, fuente(fx, 38, 500), y + 8, C_SUB)
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
            texto_centrado(d, W, p.subtitulo, fuente(fx, 36, 500), y, C_SUB)
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
