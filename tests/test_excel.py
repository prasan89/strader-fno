import tempfile
import unittest
from pathlib import Path
from xml.etree.ElementTree import fromstring
from zipfile import ZipFile

from strader_fno.excel import export_levels, NS


def snapshot():
    entry = {'symbol': '=SAFE', 'previous_high': '102.25', 'previous_low': '98.05', 'previous_close': '100.10',
             'upper_outer': '104.27', 'lower_outer': '96.03', 'upper_inner': '103.34', 'lower_inner': '96.96',
             'upper_reference': '101.37', 'lower_reference': '98.93', 'liquidity_score': '123456.70',
             'previous_session': '2026-11-11', 'instrument_key': 'NSE_EQ|TEST'}
    return {'session': '2026-11-12', 'previous_session': '2026-11-11', 'strategy': 'midpoint-range-v1',
            'selected_count': 200, 'eligible_count': 218, 'ranking_window': 20, 'ranking': 'mean(close * volume)',
            'instruments': [dict(entry, instrument_key=f'NSE_EQ|TEST{i}') for i in range(200)]}


class ExcelTests(unittest.TestCase):
    def test_real_excel_package_has_all_200_numeric_levels_and_chart_comparison_formulas(self):
        with tempfile.TemporaryDirectory() as directory:
            path = export_levels(snapshot(), Path(directory) / 'levels.xlsx')
            with ZipFile(path) as archive:
                self.assertIsNone(archive.testzip())
                for name in archive.namelist():
                    fromstring(archive.read(name))
                workbook = fromstring(archive.read('xl/workbook.xml'))
                self.assertEqual(['Levels', 'Compare JFT', 'Formula and sources'],
                                 [s.attrib['name'] for s in workbook.findall(f'{{{NS}}}sheets/{{{NS}}}sheet')])
                levels = fromstring(archive.read('xl/worksheets/sheet1.xml'))
                rows = levels.findall(f'{{{NS}}}sheetData/{{{NS}}}row')
                self.assertEqual(200, sum(int(r.attrib['r']) >= 6 for r in rows))
                cells = {c.attrib['r']: c for c in levels.findall(f'.//{{{NS}}}c')}
                self.assertEqual('104.27', cells['J6'].find(f'{{{NS}}}v').text)
                self.assertEqual('96.03', cells['K205'].find(f'{{{NS}}}v').text)
                self.assertEqual('inlineStr', cells['B6'].attrib['t'])
                self.assertIsNone(cells['B6'].find(f'{{{NS}}}f'))
                comparison = fromstring(archive.read('xl/worksheets/sheet2.xml'))
                cells = {c.attrib['r']: c for c in comparison.findall(f'.//{{{NS}}}c')}
                self.assertEqual('IF(I6="","",ROUND(I6-C6,2))', cells['O6'].find(f'{{{NS}}}f').text)
                self.assertEqual('IF(N205="","",ROUND(N205-H205,2))', cells['T205'].find(f'{{{NS}}}f').text)
                self.assertIn('COUNT(I6:N6)<6', cells['U6'].find(f'{{{NS}}}f').text)
                self.assertIsNone(cells['I6'].find(f'{{{NS}}}v'))

    def test_automatic_export_preserves_existing_comparison_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'levels.xlsx'
            path.write_bytes(b'user-edited-workbook')
            export_levels(snapshot(), path)
            self.assertEqual(b'user-edited-workbook', path.read_bytes())
            export_levels(snapshot(), path, overwrite=True)
            with ZipFile(path) as archive:
                self.assertIsNone(archive.testzip())
