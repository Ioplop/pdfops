from dataclasses import dataclass
from typing import Any
import fitz

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