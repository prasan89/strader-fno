"""Small dependency-free XLSX export of the exact cached scanner levels."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring
from zipfile import ZIP_DEFLATED, ZipFile

from .models import IST

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
FIELDS = ('upper_outer', 'lower_outer', 'upper_inner', 'lower_inner', 'upper_reference', 'lower_reference')
LABELS = ('Upper outer', 'Lower outer', 'Upper inner', 'Lower inner', 'Upper reference', 'Lower reference')


def column(index):
    result = ''
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def xml(root):
    return tostring(root, encoding='utf-8', xml_declaration=True)


def cell(row, index, value, style=0, formula=False):
    item = SubElement(row, 'c', r=f'{column(index)}{row.attrib["r"]}', s=str(style))
    if value is None:
        return
    if formula:
        SubElement(item, 'f').text = value
    elif isinstance(value, (Decimal, int)):
        if not Decimal(value).is_finite():
            raise ValueError('Spreadsheet numbers must be finite')
        SubElement(item, 'v').text = str(value)
    else:
        item.set('t', 'inlineStr')
        SubElement(SubElement(item, 'is'), 't').text = str(value)


def sheet(title, subtitle, headers, data, widths, *, formulas=None, inputs=()):
    root = Element('worksheet', xmlns=NS)
    views = SubElement(root, 'sheetViews')
    view = SubElement(views, 'sheetView', workbookViewId='0')
    SubElement(view, 'pane', ySplit='5', topLeftCell='A6', activePane='bottomLeft', state='frozen')
    SubElement(root, 'sheetFormatPr', defaultRowHeight='18')
    columns = SubElement(root, 'cols')
    for index, width in enumerate(widths, 1):
        SubElement(columns, 'col', min=str(index), max=str(index), width=str(width), customWidth='1')
    rows = SubElement(root, 'sheetData')
    cell(SubElement(rows, 'row', r='1', ht='25', customHeight='1'), 1, title, 2)
    cell(SubElement(rows, 'row', r='2', ht='32', customHeight='1'), 1, subtitle, 3)
    cell(SubElement(rows, 'row', r='3'), 1, 'All prices INR. Candidate Midpoint Range; exact JFT formula unconfirmed.', 3)
    header = SubElement(rows, 'row', r='5', ht='28', customHeight='1')
    for index, value in enumerate(headers, 1):
        cell(header, index, value, 1)
    for number, values in enumerate(data, 6):
        row = SubElement(rows, 'row', r=str(number))
        for index, value in enumerate(values, 1):
            expression = formulas(number, index) if formulas else None
            style = 5 if index in inputs else 4 if isinstance(value, Decimal) or expression and index != 21 else 0
            cell(row, index, expression if expression else value, style, bool(expression))
    if data:
        SubElement(root, 'autoFilter', ref=f'A5:{column(len(headers))}{len(data) + 5}')
    merges = SubElement(root, 'mergeCells', count='3')
    for number in (1, 2, 3):
        SubElement(merges, 'mergeCell', ref=f'A{number}:{column(len(headers))}{number}')
    if inputs and data:
        validation = SubElement(root, 'dataValidations', count='1')
        rule = SubElement(validation, 'dataValidation', type='decimal', operator='greaterThan',
                          allowBlank='1', showErrorMessage='1', errorTitle='Enter a price',
                          error='Enter a positive numeric JFT chart level.',
                          sqref=f'{column(min(inputs))}6:{column(max(inputs))}{len(data) + 5}')
        SubElement(rule, 'formula1').text = '0'
    SubElement(root, 'pageMargins', left='0.3', right='0.3', top='0.5', bottom='0.5', header='0.2', footer='0.2')
    return xml(root)


def styles():
    root = Element('styleSheet', xmlns=NS)
    formats = SubElement(root, 'numFmts', count='1')
    SubElement(formats, 'numFmt', numFmtId='164', formatCode='0.00')
    fonts = SubElement(root, 'fonts', count='3')
    for size, color, bold in ((11, 'FF17233A', False), (11, 'FFFFFFFF', True), (16, 'FF17233A', True)):
        font = SubElement(fonts, 'font')
        SubElement(font, 'sz', val=str(size))
        SubElement(font, 'color', rgb=color)
        SubElement(font, 'name', val='Calibri')
        if bold:
            SubElement(font, 'b')
    fills = SubElement(root, 'fills', count='4')
    for pattern, color in (('none', None), ('gray125', None), ('solid', 'FF244BE2'), ('solid', 'FFFFF2CC')):
        fill = SubElement(SubElement(fills, 'fill'), 'patternFill', patternType=pattern)
        if color:
            SubElement(fill, 'fgColor', rgb=color)
    borders = SubElement(root, 'borders', count='1')
    border = SubElement(borders, 'border')
    for side in ('left', 'right', 'top', 'bottom', 'diagonal'):
        SubElement(border, side)
    base = SubElement(root, 'cellStyleXfs', count='1')
    SubElement(base, 'xf', numFmtId='0', fontId='0', fillId='0', borderId='0')
    formats = SubElement(root, 'cellXfs', count='6')
    for font, fill, number in ((0, 0, 0), (1, 2, 0), (2, 0, 0), (0, 0, 0), (0, 0, 164), (0, 3, 164)):
        item = SubElement(formats, 'xf', numFmtId=str(number), fontId=str(font), fillId=str(fill), borderId='0', xfId='0')
        if number:
            item.set('applyNumberFormat', '1')
        SubElement(item, 'alignment', vertical='center', wrapText='1' if number == 0 else '0')
    names = SubElement(root, 'cellStyles', count='1')
    SubElement(names, 'cellStyle', name='Normal', xfId='0', builtinId='0')
    return xml(root)


def export_levels(snapshot: dict, path: Path, *, overwrite=False) -> Path:
    """Preserve an existing workbook, including user-entered comparison values."""
    path = Path(path)
    if path.exists() and not overwrite:
        return path
    rows = snapshot['instruments']
    if not rows or len(rows) != snapshot['selected_count'] or snapshot['strategy'] != 'midpoint-range-v1':
        raise ValueError('Invalid level snapshot for Excel export')
    title = f"200-stock level cross-check | {snapshot['session']} | selected {len(rows)}"
    subtitle = f"Previous NSE session: {snapshot['previous_session']} | {snapshot['ranking_window']}-session liquidity ranking | Generated {datetime.now(IST):%Y-%m-%d %H:%M:%S} IST"
    levels, comparison = [], []
    for rank, row in enumerate(rows, 1):
        high, low = Decimal(row['previous_high']), Decimal(row['previous_low'])
        values = [Decimal(row[field]) for field in FIELDS]
        levels.append([rank, row['symbol'], snapshot['session'], row['previous_session'], high, low,
                       Decimal(row['previous_close']), (high + low) / 2, high - low, *values,
                       Decimal(row['liquidity_score']), row['instrument_key']])
        comparison.append([rank, row['symbol'], *values, *([None] * 12), None, None])

    def comparison_formulas(row, index):
        if 15 <= index <= 20:
            actual, cached = column(index - 6), column(index - 12)
            return f'IF({actual}{row}="","",ROUND({actual}{row}-{cached}{row},2))'
        if index == 21:
            zeroes = ','.join(f'{column(i)}{row}=0' for i in range(15, 21))
            return f'IF(COUNT(I{row}:N{row})<6,"Enter all 6",IF(AND({zeroes}),"MATCH","DIFF"))'

    sheets = [
        sheet(title, subtitle,
              ['Rank', 'Symbol', 'Trading date', 'Prior session', 'Prior high', 'Prior low', 'Prior close', 'Midpoint P', 'Range R',
               *LABELS, 'Liquidity score', 'Instrument key'], levels, [8, 18, 15, 15] + [17] * 11 + [24, 30]),
        sheet('Compare with your JFT chart', 'Yellow cells: enter actual chart levels. Differences = chart value minus cached value; two-decimal comparison.',
              ['Rank', 'Symbol', *['Cached ' + name for name in LABELS], *['JFT chart ' + name for name in LABELS],
               *['Delta ' + name for name in LABELS], 'Comparison', 'Notes'], comparison, [8, 18] + [20] * 18 + [18, 36],
              formulas=comparison_formulas, inputs=tuple(range(9, 15))),
        sheet('Formula and sources', subtitle, ['Item', 'Value'], [
            ['Strategy', 'Midpoint Range v1; proprietary JFT reconstruction unconfirmed'],
            ['Input H and L', 'High and low of the previous completed NSE trading session'],
            ['Midpoint P', '(H + L) / 2'], ['Range R', 'H - L'], ['Upper/lower outer', 'P +/- 0.98 * R'],
            ['Upper/lower inner', 'P +/- 0.76 * R'], ['Upper/lower reference', 'P +/- 0.29 * R'],
            ['Rounding', 'Two decimals, ROUND_HALF_UP'], ['Data source', 'Upstox closed daily candles'],
            ['Selection', snapshot['ranking']], ['Eligible stocks', snapshot['eligible_count']],
            ['Selected stocks', snapshot['selected_count']], ['Trading session', snapshot['session']],
            ['Previous session', snapshot['previous_session']],
            ['Reference check', 'Candidate matched SBI Life screenshot; other reference stocks differed'],
            ['Comparison', 'Enter all six actual chart levels in yellow cells; MATCH means every rounded delta is zero'],
            ['Preserved edits', 'Automatic export keeps an existing dated workbook; explicit overwrite replaces edits'],
        ], [32, 110]),
    ]
    workbook = Element('workbook', xmlns=NS, attrib={'xmlns:r': REL})
    definitions = SubElement(workbook, 'sheets')
    for index, name in enumerate(('Levels', 'Compare JFT', 'Formula and sources'), 1):
        SubElement(definitions, 'sheet', name=name, sheetId=str(index), attrib={'r:id': f'rId{index}'})
    SubElement(workbook, 'calcPr', calcId='191029', fullCalcOnLoad='1', forceFullCalc='1', calcMode='auto')
    relationships = Element('Relationships', xmlns='http://schemas.openxmlformats.org/package/2006/relationships')
    for index in range(1, 4):
        SubElement(relationships, 'Relationship', Id=f'rId{index}', Type=REL + '/worksheet', Target=f'worksheets/sheet{index}.xml')
    SubElement(relationships, 'Relationship', Id='rId4', Type=REL + '/styles', Target='styles.xml')
    content = Element('Types', xmlns='http://schemas.openxmlformats.org/package/2006/content-types')
    SubElement(content, 'Default', Extension='rels', ContentType='application/vnd.openxmlformats-package.relationships+xml')
    SubElement(content, 'Default', Extension='xml', ContentType='application/xml')
    SubElement(content, 'Override', PartName='/xl/workbook.xml', ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml')
    SubElement(content, 'Override', PartName='/xl/styles.xml', ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml')
    for index in range(1, 4):
        SubElement(content, 'Override', PartName=f'/xl/worksheets/sheet{index}.xml', ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml')
    package = Element('Relationships', xmlns='http://schemas.openxmlformats.org/package/2006/relationships')
    SubElement(package, 'Relationship', Id='rId1', Type=REL + '/officeDocument', Target='xl/workbook.xml')
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.levels-', suffix='.xlsx', dir=path.parent)
    os.close(descriptor)
    try:
        with ZipFile(temporary, 'w', ZIP_DEFLATED) as archive:
            archive.writestr('[Content_Types].xml', xml(content))
            archive.writestr('_rels/.rels', xml(package))
            archive.writestr('xl/workbook.xml', xml(workbook))
            archive.writestr('xl/_rels/workbook.xml.rels', xml(relationships))
            archive.writestr('xl/styles.xml', styles())
            for index, data in enumerate(sheets, 1):
                archive.writestr(f'xl/worksheets/sheet{index}.xml', data)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path
