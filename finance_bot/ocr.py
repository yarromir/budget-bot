from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ScreenshotRecognitionResult:
    text: str
    error: str | None = None


def recognize_screenshot(path: str | Path, language: str = "rus+eng") -> ScreenshotRecognitionResult:
    """Extract text from a screenshot using local Tesseract OCR."""
    try:
        from PIL import Image, ImageOps
        import pytesseract
    except ImportError as exc:
        missing = exc.name or "pillow/pytesseract"
        return ScreenshotRecognitionResult(
            text="",
            error=(
                f"Не установлена зависимость для распознавания скриншотов: {missing}. "
                "Установи зависимости из requirements.txt и системный пакет tesseract-ocr."
            ),
        )

    image_path = Path(path)
    if not image_path.exists():
        return ScreenshotRecognitionResult(text="", error="Не смог найти скачанный скриншот.")

    try:
        with Image.open(image_path) as image:
            prepared = ImageOps.grayscale(image)
            text = pytesseract.image_to_string(prepared, lang=language)
    except pytesseract.TesseractNotFoundError:
        return ScreenshotRecognitionResult(
            text="",
            error="Tesseract OCR не найден. Установи системный пакет tesseract-ocr и языки rus/eng.",
        )
    except pytesseract.TesseractError as exc:
        return ScreenshotRecognitionResult(text="", error=f"Не смог распознать скриншот: {exc}")
    except OSError:
        return ScreenshotRecognitionResult(text="", error="Не смог открыть изображение. Пришли скриншот как фото или PNG/JPG файл.")

    cleaned = " ".join(text.split())
    if not cleaned:
        return ScreenshotRecognitionResult(text="", error="Не нашёл текст на скриншоте.")
    return ScreenshotRecognitionResult(text=cleaned)
