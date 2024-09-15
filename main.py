from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional, Union
import win32print
import unicodedata
import qrcode
import textwrap

PRINTER_WIDTH = 32  # Caracteres por línea para una impresora de 48mm | Valor por defecto

class LineItem(BaseModel):
    text: str
    bold: bool = False
    underline: bool = False
    align: str = "center"

class ProductItem(BaseModel):
    quantity: int
    description: str
    price: float
    align: str = "left"

class ColumnLayout(BaseModel):
    columns: List[str]
    data: List[List[str]]

class ParagraphItem(BaseModel):
    text: str

class TwoColumnItem(BaseModel):
    left: str
    right: str

class QRCodeItem(BaseModel):
    data: str
    size: int = 1

class TicketSection(BaseModel):
    type: str
    content: Union[List[LineItem], List[ProductItem], ColumnLayout, ParagraphItem, List[TwoColumnItem], QRCodeItem]

class Ticket(BaseModel):
    printer_name: str
    printer_width: Optional[int] = None
    sections: List[TicketSection]

def update_printer_width(printer_width: Optional[int]):
    global PRINTER_WIDTH
    if printer_width is not None:
        if printer_width == 58:
            PRINTER_WIDTH = 32
        else:
            PRINTER_WIDTH = 32            
        
def normalize_string(text):
    return unicodedata.normalize('NFKD', text).encode('ASCII', 'ignore').decode('ASCII')

def format_product_header():
    header = f"{'Cant.':^{int(PRINTER_WIDTH * 0.1)}}{'Descripción':^{int(PRINTER_WIDTH * 0.6)}}{'Total':^{PRINTER_WIDTH - int(PRINTER_WIDTH * 0.1) - int(PRINTER_WIDTH * 0.6)}}"
    return format_line(header, bold=True)

def format_line(text, bold=False, underline=False, align="center"):
    normalized_text = normalize_string(text)
    wrapped_lines = textwrap.wrap(normalized_text, width=PRINTER_WIDTH)
    
    formatted_lines = []
    for line in wrapped_lines:
        if align == "left":
            formatted = line.ljust(PRINTER_WIDTH)
        elif align == "right":
            formatted = line.rjust(PRINTER_WIDTH)
        else:  # center
            formatted = line.center(PRINTER_WIDTH)
        
        pre_commands = b""
        post_commands = b""
        if bold:
            pre_commands += b'\x1b\x45\x01'
            post_commands = b'\x1b\x45\x00' + post_commands
        if underline:
            pre_commands += b'\x1b\x2d\x01'
            post_commands = b'\x1b\x2d\x00' + post_commands

        encoded_text = formatted.encode('cp850', errors='replace')
        formatted_lines.append(pre_commands + encoded_text + post_commands + b'\n')
    
    return b''.join(formatted_lines)

def format_product(product: ProductItem):
    quantity_str = f"{product.quantity:2d}x"
    price_str = f"{product.price:.2f}"
    desc_width = PRINTER_WIDTH - len(quantity_str) - len(price_str) - 2
    description = normalize_string(product.description)[:desc_width].ljust(desc_width)
    line = f"{quantity_str} {description} {price_str}"
    return format_line(line, align=product.align)

def format_columns(layout: ColumnLayout):
    column_widths = [int(PRINTER_WIDTH * 0.1), int(PRINTER_WIDTH * 0.6), PRINTER_WIDTH - int(PRINTER_WIDTH * 0.1) - int(PRINTER_WIDTH * 0.6)]

    lines = []
    header = "".join(normalize_string(col)[:w].center(w) for col, w in zip(layout.columns, column_widths))
    lines.append(format_line(header))
    lines.append(format_line("-" * PRINTER_WIDTH))

    for row in layout.data:
        line = "".join(normalize_string(str(item))[:w].center(w) for item, w in zip(row, column_widths))
        lines.append(format_line(line))

    return b''.join(lines)

def format_paragraph(paragraph: ParagraphItem):
    wrapped_text = textwrap.wrap(normalize_string(paragraph.text), width=PRINTER_WIDTH)
    return b''.join(format_line(line, align="center") for line in wrapped_text)

def format_two_columns(item: TwoColumnItem):
    left = normalize_string(item.left)
    right = normalize_string(item.right)
    left_width = PRINTER_WIDTH // 2
    right_width = PRINTER_WIDTH - left_width
    left_formatted = left[:left_width].ljust(left_width)
    right_formatted = right[:right_width].rjust(right_width)
    return format_line(left_formatted + right_formatted)

def generate_qr_code(data: str, size: int):
    qr = qrcode.QRCode(version=1, box_size=1, border=0)
    qr.add_data(data)
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color="black", back_color="white")
    
    qr_str = ""
    for y in range(0, qr_image.size[1], 2):
        line = ""
        for x in range(qr_image.size[0]):
            upper_pixel = qr_image.getpixel((x, y)) == 0
            lower_pixel = y + 1 < qr_image.size[1] and qr_image.getpixel((x, y + 1)) == 0
            if upper_pixel and lower_pixel:
                line += "█"
            elif upper_pixel:
                line += "▀"
            elif lower_pixel:
                line += "▄"
            else:
                line += " "
        qr_str += line.center(PRINTER_WIDTH) + "\n"
    
    return qr_str.encode('cp850', errors='replace')

def format_ticket(ticket: Ticket):
    commands = []
    for section in ticket.sections:
        if section.type == 'lines':
            for line in section.content:
                commands.append(format_line(line.text, line.bold, line.underline, line.align))
        elif section.type == 'products':
            commands.append(format_product_header())
            commands.append(format_line("-" * PRINTER_WIDTH))
            for product in section.content:
                commands.append(format_product(product))
        elif section.type == 'columns':
            commands.append(format_columns(section.content))
        elif section.type == 'paragraph':
            commands.append(format_paragraph(section.content))
        elif section.type == 'two_columns':
            for item in section.content:
                commands.append(format_two_columns(item))
        elif section.type == 'qr_code':
            commands.append(generate_qr_code(section.content.data, section.content.size))
        commands.append(format_line("-" * PRINTER_WIDTH))  # Añadir línea separadora después de cada sección
            
    return b''.join(commands)

app = FastAPI()

@app.get('/printers')
async def get_printers():
    printers = [printer[2] for printer in win32print.EnumPrinters(2)]
    return printers

@app.post('/print')
async def print_ticket(ticket: Ticket):
    if ticket.printer_width!=58 or ticket.printer_width!=80:
        update_printer_width(ticket.printer_width)
    else:
        return {"status": "Printer width must be 58 or 80","paper_width":f"00 mm"}
    ticket_content = format_ticket(ticket)
    try:
        hPrinter = win32print.OpenPrinter(ticket.printer_name)
        hJob = win32print.StartDocPrinter(hPrinter, 1, ("print job", None, "RAW"))
        win32print.StartPagePrinter(hPrinter)
        win32print.WritePrinter(hPrinter, ticket_content)
        win32print.EndPagePrinter(hPrinter)
        win32print.EndDocPrinter(hPrinter)
        win32print.ClosePrinter(hPrinter)
        return {"status": "Printed successfully","paper_width":f"{PRINTER_WIDTH} mm"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)