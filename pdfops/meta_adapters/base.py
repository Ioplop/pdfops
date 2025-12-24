from abc import ABC, abstractmethod
from ..rectangles import Rectangle

class MetaAdapter(ABC):
    def __init__(self, pdf_data):
        self._pdf_data = pdf_data

    @abstractmethod
    def get_rects(self, pdf_data: bytes | None = None) -> list[Rectangle]:
        pass

    @abstractmethod
    def set_rects(self, rects: list[Rectangle], pdf_data: bytes | None = None) -> None:
        pass

    @abstractmethod
    def get_pdf(self):
        pass