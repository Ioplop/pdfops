import io, json, base64, zlib
from typing import Any
from pyhanko.pdf_utils import generic, content
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
import copy

_PDFMETA_LATEST = "/ChepssPdfmetaLatest"
_PDFMETA_HISTORY = "/ChepssPdfmetaHistory"
_PDFMETA_TYPE_KEY = "/Type"
_PDFMETA_TYPE_VAL = "/ChepssPdfmeta"

class PDFMeta:
    VERSION = "1.0"

    def __init__(self, pdf_data: bytes, b64: bool=False) -> None:
        if b64:
            pdf_data = base64.b64decode(pdf_data)
        self.pdf_data = pdf_data
        self._changed_meta = False # Flag para determinar si hay que escribir metadatos
        self._has_meta = True # Flag para determinar si había metadatos anteriormete
        self.metadata = {}
        self._read_metadata()

    def _mark_dirty(self):
        if not self._changed_meta:
            if self._has_meta:
                # Actualizar versión de metadatos solo cuando el estado cambia de NO dirty a dirty
                # y había metadatos previos.
                version = self.metadata["v"].split(".")
                if len(version) > 1:
                    last = version[-1]
                    # Incrementar versión en 1
                    new_version = ".".join(version[:-1] + [str(int(last) + 1)])
                else:
                    # Fallback: La versión no está bien escrita, pero adjuntaremos un numero de versión al final.
                    new_version = version + ".0"
                # Actualizar versión.
                self.metadata["v"] = new_version
            self._changed_meta = True

    def _create_clean_meta(self, corrupt=0):
        self.metadata = {
            "v": self.VERSION + ".0",
            "nid": 0,
            "cr": corrupt,
            "meta": []
        }
        self._has_meta = False

    def _deref(self, obj):
        # Resolve indirect objects
        return obj.get_object() if hasattr(obj, "get_object") else obj

    def _read_stream_bytes(self, stream_obj) -> bytes:
        """
        stream_obj is a generic.StreamObject (possibly indirect already deref’d)
        get_data() returns decoded bytes (applies /Filter like FlateDecode)
        """
        if hasattr(stream_obj, "data"):
            return stream_obj.data
        raise TypeError("Not a stream object")

    def _read_metadata(self):
        try:
            r = PdfFileReader(io.BytesIO(self.pdf_data), strict=False)
            root = r.root

            # Preferimos un puntero al último stream.
            target = root.get(_PDFMETA_LATEST)

            # Fallback: Encontramos el último stream en el historial
            if target is None:
                hist = root.get(_PDFMETA_HISTORY)
                if hist is not None:
                    hist = self._deref(hist)
                    if isinstance(hist, (list, generic.ArrayObject)) and len(hist) > 0:
                        target = hist[-1]

            # Final fallback: No hay metadatos. Creamos nuevos.
            if target is None:
                self._create_clean_meta(corrupt=0)
                return

            target = self._deref(target)

            # Sanidad: Aseguramos que es nuestro stream de metadatos.
            t = target.get(_PDFMETA_TYPE_KEY) if hasattr(target, "get") else None
            if t is not None:
                t = str(t)
                if t != _PDFMETA_TYPE_VAL:
                    # No nos pertenece (o alguien sobrescribió el puntero)
                    self._create_clean_meta(corrupt=1)
                    return

            # Ahora sí, leemos el stream de metadatos y lo pareamos
            raw = self._read_stream_bytes(target)
            meta = json.loads(raw.decode("utf-8"))

            # Validación mínima de estructura de metadatos
            if not isinstance(meta, dict) or "meta" not in meta or "nid" not in meta:
                self._create_clean_meta(corrupt=1)
                return

            # Asignar metadatos
            self.metadata = meta
            return

        except Exception as e:
            # Si algo falla en cualquier lugar, podemos decir que hay un problema de corrupción de metadatos.
            # Por lo tanto, creamos nuevos...
            # Se deja e por motivos de debugueo en editor.
            self._create_clean_meta(corrupt=1)

    def _write_metadata(self):
        # Solo se llama cuando self.changed_meta es True (Cuando hubo cambios en los metadatos)
        # Encodificar JSON en modo compacto.
        payload = json.dumps(self.metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        # Construir objeto stream privado de metadatos
        stream = generic.StreamObject(stream_data=payload)
        stream[generic.NameObject("/Filter")] = generic.NameObject("/FlateDecode")
        stream[generic.NameObject(_PDFMETA_TYPE_KEY)] = generic.NameObject(_PDFMETA_TYPE_VAL)
        # Optional: type hint para nuestro stream. (Innecesario pero bonito de tener)
        stream[generic.NameObject("/Subtype")] = generic.NameObject("/application#2Fjson")

        # Crear un incremental writer sobre los bytes actuales.
        w = IncrementalPdfFileWriter(io.BytesIO(self.pdf_data), strict=False)

        # Agregar stream privado como un objeto indirecto en el update incremental
        stream_ref = w.add_object(stream)

        # Actualizar punteros del catálogo
        root = w.root

        latest_key = generic.NameObject(_PDFMETA_LATEST)
        hist_key = generic.NameObject(_PDFMETA_HISTORY)

        # /ChepssPdfmetaLatest -> nuevo stream
        root[latest_key] = stream_ref

        # /ChepssPdfmetaHistory -> agregar strean al final del array
        prev_hist = root.get(hist_key)

        items = []
        if prev_hist is not None:
            prev_hist_obj = prev_hist.get_object() if hasattr(prev_hist, "get_object") else prev_hist
            if isinstance(prev_hist_obj, (list, generic.ArrayObject)):
                items = list(prev_hist_obj)

        items.append(stream_ref)
        root[hist_key] = generic.ArrayObject(items)

        # Decirle a pyHanko que cambiamos el catálogo en esta revisión incremental
        w.update_root()

        # Aplicar cambio en pdf_data
        out = io.BytesIO()
        w.write(out)
        self.pdf_data = out.getvalue()

        # Limpiar flag de cambios en metadatos
        self._changed_meta = False

    def _get_new_id(self):
        new_id = self.metadata["nid"]
        self.metadata["nid"] += 1
        return new_id

    @property
    def content(self):
        return self.metadata["meta"]

    def get_pdf(self):
        if self._changed_meta:
            self._write_metadata()
        return self.pdf_data

    def get_pdf_b64(self):
        return base64.b64encode(self.get_pdf())

    def _in_ns(self, meta : dict, ns : str | None):
        return (ns is None) or (meta.get("ns", "") == ns)

    def meta_get_id(self, uid: int, ns : str | None = None) -> dict | None:
        for meta in self.content:
            if meta.get("id") == uid and self._in_ns(meta, ns):
                return meta
        return None

    def meta_get_first(self, name: str, ns : str | None = None) -> dict | None:
        for meta in self.content:
            if meta.get("name") == name and self._in_ns(meta, ns):
                return meta
        return None

    def meta_get_multiple(self, name: str | None = None, ns: str | None = None) -> list[dict]:
        return [
            meta
            for meta in self.content
            if (name is None or meta.get("name") == name)
            and self._in_ns(meta, ns)
        ]

    def meta_edit_name(self, name: str, obj: dict, edit_first = True, ns: str | None = None) -> bool:
        meta = self.meta_get_multiple(name=name, ns=ns)
        if not meta:
            return False
        for m in meta:
            for key, value in obj.items():
                m["content"][key] = value
            if edit_first:
                break
        self._mark_dirty()
        return True

    def meta_edit_id(self, uid: int, obj: dict, ns: str | None = None) -> bool:
        meta = self.meta_get_id(uid, ns)
        if not meta:
            return False
        for key, value in obj.items():
            meta["content"][key] = value
        self._mark_dirty()
        return True

    def meta_remove_name(self, name:str, remove_all=True, ns: str | None = None) -> bool:
        i = 0
        removed = False
        while i < len(self.content):
            meta = self.content[i]
            if meta.get("name") == name and self._in_ns(meta, ns):
                del self.content[i]
                removed = True
                if not remove_all:
                    break
            else:
                i += 1
        if removed:
            self._mark_dirty()
        return removed

    def meta_remove_id(self, uid: int, ns: str | None = None) -> bool:
        i = 0
        while i < len(self.content):
            meta = self.content[i]
            if meta.get("id") == uid and self._in_ns(meta, ns):
                del self.content[i]
                self._mark_dirty()
                return True
        return False

    def meta_add(self, name: str, obj: dict, ns: str = "") -> dict:
        meta = {
            "id": self._get_new_id(),
            "ns": ns,
            "name": name,
            "content": copy.deepcopy(obj)
        }
        self.content.append(meta)
        self._mark_dirty()
        return meta

    #! Zona de funciones de DEBUG

    def meta_dump(self):
        return self.metadata

    def _is_our_meta_stream(self, stream_obj, allow_missing_type: bool = True) -> bool:
        """
        Returns True if this stream looks like one of ours.
        If allow_missing_type=True, then streams with no /Type are accepted
        (matches your current meta_dump_all behavior). :contentReference[oaicite:2]{index=2}
        """
        if not hasattr(stream_obj, "get"):
            return False
        t = stream_obj.get(_PDFMETA_TYPE_KEY)
        if t is None:
            return allow_missing_type
        return str(t) == _PDFMETA_TYPE_VAL

    def _iter_meta_stream_candidates(self, root):
        """
        Yield (source, deref_stream_obj) from /Latest and /History, deduped.
        """
        seen = set()

        def _push(source: str, obj):
            if obj is None:
                return
            # keep the indirect ref identity if possible, otherwise use python id
            key = (getattr(obj, "idnum", None), getattr(obj, "generation", None), id(obj))
            if key in seen:
                return
            seen.add(key)

            stream = self._deref(obj)
            yield (source, stream)

        # /ChepssPdfmetaLatest
        latest = root.get(_PDFMETA_LATEST)
        if latest is not None:
            yield from _push("latest", latest)

        # /ChepssPdfmetaHistory
        hist = root.get(_PDFMETA_HISTORY)
        if hist is not None:
            hist = self._deref(hist)
            if isinstance(hist, (list, generic.ArrayObject)):
                for item in hist:
                    yield from _push("history", item)

    def meta_dump_all(self, *, include_raw=False, as_json=True):
        """
        Debugging-only: enumerate every candidate meta stream we can find, and
        report what happened for each one (parsed / skipped / error).

        include_raw: include raw JSON bytes (decoded) for quick inspection
        as_json: return json string if True, else return python structure
        """
        out = {
            "ok": True,
            "items": [],
            "summary": {
                "candidates": 0,
                "accepted": 0,
                "parsed": 0,
                "skipped": 0,
                "errors": 0,
            },
        }

        def add_item(**entry):
            out["items"].append(entry)

        try:
            r = PdfFileReader(io.BytesIO(self.pdf_data), strict=False)
            root = r.root

            for source, stream in self._iter_meta_stream_candidates(root):
                out["summary"]["candidates"] += 1

                item: dict[str, Any] = {"source": source}

                # ownership/type check (debug-friendly)
                try:
                    t = getattr(stream, "get", lambda *_: None)(_PDFMETA_TYPE_KEY)
                    item["type_val"] = None if t is None else str(t)
                    is_ours = self._is_our_meta_stream(stream, allow_missing_type=True)
                except Exception as e:
                    out["summary"]["errors"] += 1
                    add_item(**item, status="error", error=f"type_check_failed: {e!r}")
                    continue

                if not is_ours:
                    out["summary"]["skipped"] += 1
                    add_item(**item, status="skipped", reason="not_our_type")
                    continue

                out["summary"]["accepted"] += 1

                # read bytes
                try:
                    raw_bytes = self._read_stream_bytes(stream)
                    item["byte_len"] = len(raw_bytes) if raw_bytes is not None else None
                except Exception as e:
                    out["summary"]["errors"] += 1
                    add_item(**item, status="error", error=f"stream_read_failed: {e!r}")
                    continue

                # decode + parse
                try:
                    raw_text = raw_bytes.decode("utf-8")
                    if include_raw:
                        item["raw"] = raw_text
                    meta = json.loads(raw_text)
                    if not isinstance(meta, dict):
                        raise TypeError(f"meta is {type(meta).__name__}, expected dict")
                    out["summary"]["parsed"] += 1
                    add_item(**item, status="parsed", meta=meta)
                except Exception as e:
                    out["summary"]["errors"] += 1
                    add_item(**item, status="error", error=f"json_parse_failed: {e!r}")

        except Exception as e:
            out["ok"] = False
            out["summary"]["errors"] += 1
            add_item(status="fatal", error=f"pdf_read_failed: {e!r}")

        return json.dumps(out, indent=4, ensure_ascii=False) if as_json else out

