from .base import MetaAdapter
from .. import Rectangle, PDFMeta

class StreamObjectMetaAdapter(MetaAdapter):
    def __init__(self, pdf_data: bytes, rect_namespace="pdfops.rect"):
        super().__init__(pdf_data)
        self.rns = rect_namespace

    def get_rects(self, pdf_data: bytes | None = None) -> list[Rectangle]:
        meta = PDFMeta(pdf_data or self._pdf_data, b64=False)
        rects = meta.meta_get_multiple(ns=self.rns)
        return [Rectangle(r["name"], **r["content"]) for r in rects]

    def set_rects(self, rects: list[Rectangle], pdf_data: bytes | None = None) -> None:
        meta = PDFMeta(pdf_data or self._pdf_data, b64=False)
        # Remover rectángulos antiguos
        prev_rects = meta.meta_get_multiple(ns=self.rns)
        for r in prev_rects:
            meta.meta_remove_id(r["id"], ns=self.rns)
        # Crear rectángulos nuevos
        for rect in rects:
            meta.meta_add(name=rect.name, ns=self.rns, obj=rect.as_dict(include_name=False))
        self._pdf_data = meta.get_pdf()

    def get_pdf(self):
        return self._pdf_data
