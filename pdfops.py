# -*- coding: utf-8 -*-
from dataclasses import dataclass, fields
import base64
import fitz
from pdfmeta import PDFMeta
from typing import Any
import json

RECT_NAMESPACE = "pdfops.rect"

def _bytes_to_b64(byte_data: bytes) -> bytes:
    return base64.b64encode(byte_data)

def _b64_to_bytes(b64_data: bytes) -> bytes:
    return base64.b64decode(b64_data)

@dataclass
class Rectangle:
    name: str
    category: str
    page: int
    x1 : float
    y1 : float
    x2 : float
    y2 : float

    def __init__(self, name, category, page, x1, y1, x2, y2):
        self.name = str(name)
        self.category = str(category)
        self.page = int(page)
        self.x1, self.x2 = min(x1, x2), max(x1, x2)
        self.y1, self.y2 = min(y1, y2), max(y1, y2)

    @property
    def height(self) -> float:
        return abs(self.y2 - self.y1)

    @property
    def width(self) -> float:
        return abs(self.x2 - self.x1)

    def as_dict(self, include_name = True) -> dict:
        data : dict[str, Any] = {
            "page": self.page,
            "category": self.category,
            "x1": self.x1,
            "y1": self.y1,
            "x2": self.x2,
            "y2": self.y2,
        }
        if include_name:
            data["name"] = self.name
        return data

    def as_fitz(self) -> fitz.Rect:
        return fitz.Rect(self.x1, self.y1, self.x2, self.y2)

class PDFProcessor:
    H_ALIGNS = {
        "left": fitz.TEXT_ALIGN_LEFT,
        "right": fitz.TEXT_ALIGN_RIGHT,
        "center": fitz.TEXT_ALIGN_CENTER,
        "justify": fitz.TEXT_ALIGN_JUSTIFY
    }

    @dataclass
    class PPDimension:
        x0 : float
        y0 : float
        w: float
        h: float

    def reload_dimensions(self):
        self.dimensions = []
        for p in range(len(self.doc)):
            page = self.doc[p]
            rect = page.rect
            self.dimensions.append(PDFProcessor.PPDimension(rect.x0, rect.y0, rect.width, rect.height))

    def __init__(self, pdf_data: bytes, b64: bool) -> None:
        self._pdf_data = _b64_to_bytes(pdf_data) if b64 else pdf_data
        self.dirty_fitz = False
        self.dirty_meta = False
        self._doc = fitz.open("pdf", self._pdf_data)
        self.dimensions: list[PDFProcessor.PPDimension] = []
        self.reload_dimensions()
        self._closed = False
        self.rects : list[Rectangle] = []
        self._load_rects()

    def __enter__(self) -> "PDFProcessor":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.doc.close()
        self._closed = True
        #TODO: Make everything raise when accessed after closing, except for a small allow list.

    def _load_rects(self):
        meta = PDFMeta(self._pdf_data, b64=False)
        rects = meta.meta_get_multiple(ns=RECT_NAMESPACE)
        self.rects = [Rectangle(r["name"], **r["content"]) for r in rects]

    def _save_rects(self):
        meta = PDFMeta(self._pdf_data, b64=False)
        # Remover rectángulos antiguos
        prev_rects = meta.meta_get_multiple(ns=RECT_NAMESPACE)
        for r in prev_rects:
            meta.meta_remove_id(r["id"], ns=RECT_NAMESPACE)
        # Crear rectángulos nuevos
        for rect in self.rects:
            meta.meta_add(name=rect.name, ns=RECT_NAMESPACE, obj=rect.as_dict(include_name=False))
        self._pdf_data = meta.get_pdf()
        self.dirty_meta = False

    @property
    def pdf_data(self) -> bytes:
        if self.dirty_fitz:
            self._pdf_data = self.doc.write()
            self.dirty_fitz = False
        if self.dirty_meta:
            self.doc.close()
            self._save_rects()
            self._doc = fitz.open("pdf", self._pdf_data)
        return self._pdf_data

    @property
    def doc(self) -> fitz.Document:
        if self._doc is None or self._doc.is_closed:
            raise RuntimeError("PDF document is closed but then used!")
        return self._doc

    @staticmethod
    def from_bytes(pdf_data: bytes) -> 'PDFProcessor':
        return PDFProcessor(pdf_data, b64=False)

    @staticmethod
    def from_b64(pdf_data: bytes) -> 'PDFProcessor':
        return PDFProcessor(pdf_data, b64=True)

    def get_pdf(self):
        return self.pdf_data

    def get_pdf_b64(self):
        return _bytes_to_b64(self.pdf_data)

    def meta_store_rect(self, rect: Rectangle) -> None:
        # Add rectangle to metadata
        if self.meta_find_rect(rect.name):
            raise ValueError(f"Rectangle {rect.name} already exists!")
        self.rects.append(rect)
        self.dirty_meta = True

    def meta_store_rect_data(self,
                             name: str,
                             page: int,
                             x1: float,
                             y1: float,
                             x2: float,
                             y2: float,
                             category: str = "") -> None:
        return self.meta_store_rect(Rectangle(name=name, category=category, page=page, x1=x1, y1=y1, x2=x2, y2=y2))

    def meta_find_rect(self, name: str, category: str | None = None) -> Rectangle | None:
        for r in self.rects:
            if r.name == name and (category is None or r.category == category):
                return r
        return None

    def meta_find_rects_by_category(self, category: str) -> list[Rectangle]:
        return [
            r
            for r in self.rects
            if r.category == category
        ]

    def meta_remove_rect(self, name: str, *, category: str | None = None, first_only: bool = False) -> None:
        """
        Remove rectangles with the given name.

        :param name: Name of the rectangle(s) to remove.
        :param category: If specified, only remove rectangles with the given category.
        :param first_only: If True, remove only the first matching entry.
                           If False (default), remove all matches (legacy behavior).
        """
        i = 0
        while i < len(self.rects):
            if self.rects[i].name == name and (category is None or self.rects[i].category == category):
                self.rects.pop(i)
                self.dirty_meta = True
                if first_only:
                    return
            else:
                i += 1

    def meta_edit_rect_data(self, name: str, page: int, x1: float, y1: float, x2: float, y2: float, category: str) -> None:
        rect = Rectangle(name=name, category=category, page=page, x1=x1, y1=y1, x2=x2, y2=y2)
        self.meta_edit_rect(rect)

    def meta_edit_rect(self, rect: Rectangle) -> None:
        original = self.meta_find_rect(rect.name)
        # Si el rectángulo ya existe, lo actualizamos, si no, lo creamos
        if original is None:
            self.meta_store_rect(rect)
        else:
            # Esta es una manera de copiar todos los campos de un rectángulo.
            for f in fields(rect):
                setattr(original, f.name, getattr(rect, f.name))
        self.dirty_meta = True

    def page_dimensions(self, page: int) -> tuple[float, float]:
        dim = self.dimensions[page]
        return dim.w, dim.h

    def norm_to_point(self, page: int, nx: float, ny: float) -> tuple[float, float]:
        dim = self.dimensions[page]
        return dim.x0 + nx * dim.w, dim.y0 + ny * dim.h

    def norm_to_point_rect(self, rect: Rectangle) -> Rectangle:
        x1, y1 = self.norm_to_point(rect.page, rect.x1, rect.y1)
        x2, y2 = self.norm_to_point(rect.page, rect.x2, rect.y2)
        return Rectangle(
            name=rect.name,
            category=rect.category,
            page=rect.page,
            x1=x1,
            y1=y1,
            x2=x2,
            y2=y2
        )

    def point_to_norm(self, page, x, y) -> tuple[float, float]:
        dim = self.dimensions[page]
        return (x - dim.x0)/dim.w, (y - dim.y0)/dim.h

    def point_to_norm_rect(self, rect: Rectangle) -> Rectangle:
        x1, y1 = self.point_to_norm(rect.page, rect.x1, rect.y1)
        x2, y2 = self.point_to_norm(rect.page, rect.x2, rect.y2)
        return Rectangle(
            name=rect.name,
            category=rect.category,
            page=rect.page,
            x1=x1,
            y1=y1,
            x2=x2,
            y2=y2
        )

    def _measure_text_remaining_space(self, text, font_size, font, rect, wrap: bool, color=(0, 0, 0), h_align="left") -> tuple[float, float]:
        # Primero crearemos un documento auxiliar con una hoja para inyectar el documento, basándonos en la hoja real
        # Crear documento auxiliar
        norm_rect = self.norm_to_point_rect(rect)
        if not text:
            return norm_rect.height, norm_rect.width
        # Dependiendo del modo de wrapping, calculamos el espacio horizontal y vertical de formas distintas.
        if not wrap:
            # Encontrar fuente
            font_obj = fitz.Font(font)

            # Buscamos el la línea de texto más ancha para encontrar el espacio horizontal
            lines = text.split("\n")
            n_lines = max(1, len(lines))
            max_width = max(
                font_obj.text_length(line, fontsize=font_size)
                for line in lines
            )
            horizontal_space = norm_rect.width - max_width

            # Aquí sumamos la altura total del texto basado en el número de líneas.
            # Calcular altura por línea
            ascent = font_obj.ascender
            descent = font_obj.descender
            em_height = ascent - descent
            # Calculo interlineado
            base_line_height = em_height * font_size
            leading_factor = 1.0
            line_height = base_line_height * leading_factor
            total_text_height = n_lines * line_height
            vertical_space = norm_rect.height - total_text_height
        else:
            # Crear hoja auxiliar para simular dibujo de texto
            aux_doc = fitz.open()
            pw, ph = self.norm_to_point(rect.page, 1, 1)
            aux_page: fitz.Page = aux_doc.new_page(width=pw, height=ph)
            # Al dibujar un textbox, recibimos directamente el espacio vertical sobrante.
            vertical_space = aux_page.insert_textbox(
                norm_rect.as_fitz(),
                text,
                fontsize=font_size,
                fontname=font,
                color=color,
                align=self.H_ALIGNS.get(h_align, fitz.TEXT_ALIGN_LEFT)
            )
            # Podemos ignorar horizontal_space porque insert_textbox tiene la opción de alinear el texto horizontalmente.
            horizontal_space = 0
            aux_doc.close()
        return vertical_space, horizontal_space

    def insert_image_in_rect(
            self,
            rect_name: str,
            image_data: bytes,
            b64: bool = True,
            keep_proportion: bool = True,
            overlay: bool = True,
    ) -> None:
        """
        Insert an image into the given rectangle (by name).

        rect: rectangle name.
        image_data: image file bytes (PNG/JPEG/etc).
        b64: if True, image_data is base64-encoded bytes.
        keep_proportion: whether to preserve image aspect ratio.
        overlay: if True, draw on top of existing content.
        """
        if b64:
            image_data = base64.b64decode(image_data)

        rect = self.meta_find_rect(rect_name)
        if rect is None:
            raise KeyError(f"Can't find {rect_name}")
        page = self.doc.load_page(rect.page)  # 0-based index
        fr = self.norm_to_point_rect(rect).as_fitz()

        page.insert_image(
            fr,
            stream=image_data,  # use in-memory bytes
            keep_proportion=keep_proportion,
            overlay=overlay,
        )

        self.dirty_fitz = True

    @staticmethod
    def _text_fits_in_rect(
            rect: fitz.Rect,
            text: str,
            fontsize: float,
            fontname: str | None,
            color: tuple[float, float, float],
            align: int = fitz.TEXT_ALIGN_LEFT,
    ) -> bool:
        """
        Return True if the text *fits* in the given rect at this fontsize,
        according to insert_textbox. Uses a temporary page/document.
        """
        tmp_doc = fitz.open()
        tmp_page = tmp_doc.new_page(width=rect.width, height=rect.height)

        rc = tmp_page.insert_textbox(
            rect,
            text,
            fontsize=fontsize,
            fontname=fontname,
            color=color,
            align=align,
        )
        tmp_doc.close()
        return rc >= 0

    def insert_text_in_rect_autoshrink(
            self,
            rect_name: str,
            text: str,
            max_font_size: float = 20,
            min_font_size: float = 1,
            color: tuple[float, float, float] = (0, 0, 0),
            font: str | None = "helv",
            h_align="left",
            v_align="top",  # "top", "center", "bottom"
    ) -> float | None:
        """
        Insert text into rect, shrinking font size as needed so *all* text
        fits in that rectangle.

        Returns the font size actually used, or None if nothing fits
        even at min_font_size.
        """
        rect = self.meta_find_rect(rect_name)
        if rect is None:
            raise KeyError(f"Rectangle not found: {rect_name}")

        abs_rect = self.norm_to_point_rect(rect)
        fr = abs_rect.as_fitz()
        page = self.doc.load_page(abs_rect.page)

        align = self.H_ALIGNS.get(h_align, fitz.TEXT_ALIGN_LEFT)
        fontname = font

        # Búsqueda binaria para encontrar el máximo tamaño posible que quepa en el rectángulo
        lo, hi = min_font_size, max_font_size
        best_size: float | None = None

        while lo <= hi:
            mid = (lo + hi) / 2.0
            fits = PDFProcessor._text_fits_in_rect(
                fr,
                text,
                fontsize=mid,
                fontname=fontname,
                color=color,
                align=align,
            )
            if fits:
                best_size = mid
                lo = mid + 0.5  # try bigger
            else:
                hi = mid - 0.5  # try smaller

        if best_size is None:
            return None

        best_size = max(min(best_size, max_font_size), min_font_size)
        # --- medir altura real del bloque de texto para best_size ---
        tmp_doc = fitz.open()
        # Usamos la MISMA altura del rectángulo real (fr.height)
        tmp_page = tmp_doc.new_page(width=fr.width, height=fr.height)
        measure_rect = fitz.Rect(0, 0, fr.width, fr.height)

        rc = tmp_page.insert_textbox(
            measure_rect,
            text,
            fontsize=best_size,
            fontname=fontname,
            color=color,
            align=align,
        )
        tmp_doc.close()

        if rc < 0:
            # No debería pasar porque _text_fits_in_rect ya comprobó que cabe,
            # pero por seguridad:
            text_height = fr.height
        else:
            # rc = altura del SUB-rectángulo vacío al fondo
            # altura usada = altura total - espacio vacío
            text_height = fr.height - rc
            if text_height < 0:
                text_height = 0

        # --- ajustar verticalmente según v_align ---
        v_align = (v_align or "top").lower()
        if v_align == "center":
            top_y = fr.y0 + (fr.height - text_height) / 2.0
        elif v_align == "bottom":
            top_y = fr.y1 - text_height
        else:  # "top" (default)
            top_y = fr.y0

        target_rect = fitz.Rect(x0=fr.x0, y0=top_y, x1=fr.x1, y1=top_y + text_height)

        # 2) Dibujar el texto una vez en la posición final
        page.insert_textbox(
            target_rect,
            text,
            fontsize=best_size,
            fontname=fontname,
            color=color,
            align=align,
        )

        self.dirty_fitz = True
        return best_size

    def insert_text_in_rect(
            self,
            rect_name: str,
            text: str,
            font_size: float = 12,
            wrap: bool = True,
            color: tuple[float, float, float] = (0, 0, 0),  # RGB (floats 0–1)
            font: str = "Helvetica",  # closest to Odoo's default look
            h_align: str = "left",
            v_align: str = "top" # top - center - bottom
    ) -> None:
        """
        Inserta texto en un rectángulo.
        """
        if h_align not in self.H_ALIGNS:
            raise ValueError(f"Invalid align value: {h_align} - Use one of: {self.H_ALIGNS.keys()}")
        if v_align not in ("top", "center", "bottom"):
            raise ValueError(f"Invalid v_align value: {v_align} - Use one of: top, center, bottom")

        rect = self.meta_find_rect(rect_name)
        if rect is None:
            raise KeyError(f"Rectangle not found: {rect_name}")

        # Convertir rectángulo normalizado a puntos
        point_rect = self.norm_to_point_rect(rect)
        # Abrir página correspondiente
        page = self.doc.load_page(point_rect.page)

        vertical_space, horizontal_space = self._measure_text_remaining_space(text, font_size, font, rect, wrap, (0, 0, 0), h_align)
        # Alinear verticalmente
        if v_align != "top":
            # Medir espacio vertical sobrante bajo el texto:
            if v_align == "center":
                point_rect.y1 += vertical_space / 2
            elif v_align == "bottom":
                point_rect.y1 += vertical_space
        # Alinear horizontalmente
        if not wrap:
            if h_align != "left":
                if h_align == "center":
                    point_rect.x1 += horizontal_space / 2
                elif h_align == "right":
                    point_rect.x1 += horizontal_space

        fr = point_rect.as_fitz()
        if wrap:
            # insert_textbox handles wrapping automatically
            h_align = self.H_ALIGNS.get(h_align, fitz.TEXT_ALIGN_LEFT)
            page.insert_textbox(
                fr,
                text,
                fontsize=font_size,
                fontname=font,
                color=color,
                align=h_align,
            )
        else:
            # No wrapping → single line
            page.insert_text(
                (fr.x0, fr.y0 + font_size),  # baseline = y0 + font_size
                text,
                fontsize=font_size,
                fontname=font,
                color=color,
            )

        self.dirty_fitz = True

    def meta_dump(self) -> str:
        return json.dumps([
            r.as_dict()
            for r in self.rects
        ], indent=2)

    def append_text_in_rect(
            self,
            rect_name: str,
            text: str,
            max_font_size: float = 20,
            min_font_size: float = 1,
            color: tuple[float, float, float] = (0, 0, 0),
            font: str = "helv",
    ) -> float | None:
        """
        Append left-aligned text inside a named rectangle.

        Behavior:
          - Vertically center the text inside the rect.
          - Use the largest font size possible up to max_font_size.
          - Do NOT wrap text: a single line that may overflow the rect to the right.
          - After inserting, move the rect's left edge to the end of the text, so
            the next append starts right after the previous text.

        Returns:
            The font size used, or None if nothing fits vertically.
        """

        rect = self.meta_find_rect(rect_name)
        if rect is None:
            raise KeyError(f"Rectangle not found: {rect_name}")

        # Current "remaining" rect in absolute coordinates
        abs_rect = self.norm_to_point_rect(rect)
        fr = abs_rect.as_fitz()
        page = self.doc.load_page(abs_rect.page)

        # Find the tallest font that fits the rect height
        # We don't care about width here (no wrapping), so we
        # give a very wide test box with the same height.
        test_rect = fitz.Rect(0, 0, fr.height * 1000, fr.height)

        lo, hi = min_font_size, max_font_size
        best: float | None = None

        while lo <= hi:
            mid = (lo + hi) / 2.0
            fits = PDFProcessor._text_fits_in_rect(
                test_rect,
                text,
                fontsize=mid,
                fontname=font,
                color=color,
                align=fitz.TEXT_ALIGN_LEFT,
            )
            if fits:
                best = mid
                lo = mid + 0.5  # try bigger
            else:
                hi = mid - 0.5  # try smaller

        if best is None:
            # Nothing fits vertically
            return None

        fontsize = best

        # Measure the text width for this font size
        # IMPORTANT: usar fuente porque fitz no soporta tildes
        font_obj = fitz.Font(font)
        tw = font_obj.text_length(text, fontsize=fontsize)

        # Compute baseline for vertical centering
        text_height = fontsize  # good approximation here
        y_center = fr.y0 + (fr.height - text_height) / 2.0
        baseline_y = y_center + text_height

        # Draw single-line text, no wrapping
        page.insert_text(
            (fr.x0, baseline_y),
            text,
            fontsize=fontsize,
            fontname=font,
            color=color,
        )

        # Move the rect so the next call starts at the end of this text
        end_x_abs = fr.x0 + tw  # absolute X at end of drawn text

        # Convert that absolute X back to normalized coordinates
        new_x_norm, _ = self.point_to_norm(rect.page, end_x_abs, fr.y0)

        # We collapse the rect horizontally at the end point:
        #  - y1, y2 stay the same (same vertical band)
        #  - x1 == x2 == end of text
        updated_rect = Rectangle(
            name=rect.name,
            category=rect.category,
            page=rect.page,
            x1=new_x_norm,
            y1=rect.y1,
            x2=new_x_norm,
            y2=rect.y2,
        )
        self.meta_edit_rect(updated_rect)

        # Snapshot updated PDF
        self.dirty_fitz = True
        return fontsize

    def define_rects_from_text(
        self,
        text: str,
        base_name: str,
        page: int | None = None,
        *,
        multiple: bool = False,
        store: bool = True,
        start_index: int = 1,
        category: str = ""
    ) -> list[Rectangle]:
        """
        Search for `text` in the PDF and define rectangle regions for each match.

        Args:
            text: The text to search for (may appear multiple times).
            base_name: Base name for generated rectangles.
                       e.g. "Firma" -> "Firma1", "Firma2", ...
            page: 0-based page index to search in. If None, search all pages.
            multiple:
                - True  -> create a rect for every occurrence (Name1, Name2, ...)
                - False -> only the first occurrence (named exactly base_name)
            store:
                - True  -> persist each rect into metadata via meta_store_rect
                - False -> just return them without persisting
            start_index:
                Starting index for enumerated names when multiple=True.
            category: Category for all generated rectangles.

        Returns:
            List[Rectangle] with all defined rectangles (at least one).

        Raises:
            KeyError if no occurrences of `text` are found.
        """
        # Lista de todos los rectángulos encontrados
        rects: list[Rectangle] = []
        # Lista de todos los números de página por iterar.
        pages = [page] if page is not None else range(len(self.doc))
        # Contador de rectángulos
        counter = start_index
        for pno in pages:
            pg = self.doc.load_page(pno)
            # Lista de rectángulos encontrados en formato fitz
            hits = pg.search_for(text)

            for hit in hits:
                # Si ya encontramos uno, y solo queremos uno, nos salimos.
                if not multiple and rects:
                    break

                # Convertir puntos fitz a puntos normalizados
                x1, y1 = self.point_to_norm(pno, hit.x0, hit.y0)
                x2, y2 = self.point_to_norm(pno, hit.x1, hit.y1)

                # Cuando se quiere encontrar multiples rectángulos, debemos agregar numero de rectángulo al final del
                # nombre. Si no, es solo el nombre y nada más.
                if multiple:
                    name = f"{base_name}{counter}"
                    counter += 1
                else:
                    name = base_name

                # Crear el rectángulo automáticamente garantiza que x1 e y1 sean menores que x2 y y2 respectivamente.
                rect = Rectangle(name=name, category=category, page=pno, x1=x1, y1=y1, x2=x2, y2=y2)
                rects.append(rect)

                # Si queremos almacenar los rectángulos, usamos función meta
                if store:
                    self.meta_store_rect(rect)

            if not multiple and rects:
                # Salida si es que queremos solo un rectángulo y ya lo encontramos.
                break

        # Si no hubo rectángulos encontramos, arrojamos error por llave. Permite encontrar errores inmediatamente.
        if not rects:
            raise KeyError(f"Text {text!r} not found in PDF")

        # Retornamos los rectángulos encontrados. En la mayoría de los casos, esto no lo usaremos porque las funciones
        # de esta clase permiten usar los rectángulos directamente desde metadatos, pero quizás por algún motivo queramos
        # acceder a ellos directamente para hacer otro proceso, como cambiar el tamaño. En cuyo caso, la opción está.
        return rects

    def _drflt(self, r_name: str, text: str, page: int, lines: list[Rectangle], category: str="") -> list[Rectangle]:
        """
        Función puramente interna, auxiliar, para define_rects_from_long_text.
        """
        if not text:
            return []
        # Buscar texto.
        rects = []
        search = text
        trail = ""
        while search and not rects:
            try:
                rects = self.define_rects_from_text(search, r_name, page, multiple=False, store=False, category=category)
            except KeyError:
                rects = []
            if not rects:
                trail = search[-1] + trail
                search = search[:-1]
        if rects:
            lines.extend(rects)
        else:
            raise KeyError(f"Text {text} not found in PDF")

        if trail:
            return self._drflt(r_name, trail, page, lines, category=category)
        else:
            return lines

    def define_rects_from_long_text(self, r_name: str, text: str, page: int, max_lines=0, category: str="") -> list[Rectangle]:
        """
        Esta función divide el texto hasta encontrarlo. Esto permite encontrar textos que tienen wrapping
        debido a que son muy largos.
        Sin embargo, corre el riesgo de encontrar otras cosas si el texto restante coincide.
        Por ejemplo, si buscamos HolaMundoComoEstás

        Y tienes un texto que dice:
        Estás

        HolaMundoComo
        Estás

        Entonces encontraremos dos rectángulos, uno sobre HolaMundoComo
        Y otro sobre EL PRIMER Estás
        """
        text_lines = text.splitlines()
        all_rects = [
            rect
            for line in text_lines
            for rect in self._drflt(r_name, line.strip(), page, [], category=category)
        ]
        count = 1
        if 0 < max_lines < len(all_rects):
            raise KeyError(f"No se pudo encontrar {text} en pdf en menos de {max_lines}")
        if len(all_rects) > 0:
            for rect in all_rects:
                rect.name = rect.name + f"{count}"
                count += 1

        for rect in all_rects:
            self.meta_store_rect(rect)
        return all_rects

    def create_clickable_link(self, rect_name: str, url: str):
        rect = self.meta_find_rect(rect_name)
        if rect is None:
            raise KeyError(f"Rectangle not found: {rect_name}")
        page = self.doc.load_page(rect.page)
        page.insert_link({
            "kind": fitz.LINK_URI,
            "from": self.norm_to_point_rect(rect).as_fitz(),
            "uri": url
        })
        self.dirty_fitz = True