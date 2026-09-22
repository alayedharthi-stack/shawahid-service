"""Opt-in real Chromium/PyMuPDF integration tests (no service or network).

SHAWAHID_PDF_TESTS=1 python -m unittest discover -s tests/document_templates
Install the requirements' Playwright Chromium first.
"""
import copy
import json
import os
import re
from pathlib import Path
import unittest
from unittest.mock import patch

from app.document_templates import export_pdf
from app.document_templates.renderer import render_document

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / 'examples/documents/v1'


def fixture(name):
    return json.loads((EXAMPLES / name).read_text(encoding='utf8'))


@unittest.skipUnless(os.environ.get('SHAWAHID_PDF_TESTS') == '1', 'real PDF tests are opt-in')
class PdfIntegrationTest(unittest.TestCase):
    def test_all_reviewed_samples_page_sizes_and_no_blank_pages(self):
        import fitz
        cases = [('worksheet', False, 1), ('worksheet', True, 1),
                 ('exam', False, 2), ('exam', True, 1), ('weekly-plan', False, 1)]
        for name, key, count in cases:
            with self.subTest(name=name, answers=key):
                document = fixture(name + '.json')
                answers = fixture(name + '.answers.json') if key else None
                with fitz.open(stream=export_pdf(document, answers=answers), filetype='pdf') as pdf:
                    self.assertEqual(len(pdf), count)
                    for page in pdf:
                        expected = (842, 595) if name == 'weekly-plan' else (595, 842)
                        self.assertAlmostEqual(page.rect.width, expected[0], delta=1)
                        self.assertAlmostEqual(page.rect.height, expected[1], delta=1)
                        self.assertGreater(len(page.get_text().strip()), 150)
                        self.assertTrue(page.get_fonts())
                    if not key and name != 'weekly-plan':
                        self.assertEqual(sum(len(page.get_images()) for page in pdf), 1)

    def test_many_questions_keep_last_answer_line_with_question(self):
        import fitz
        for kind in ('worksheet', 'exam'):
            with self.subTest(kind=kind):
                document = fixture('worksheet.json')
                source = document['questions']
                document['kind'] = kind
                document['questions'] = []
                for n in range(24):
                    q = copy.deepcopy(source[n % len(source)])
                    q.update(id=f'case{n:02}', text=f'BEGIN_{n:02} ' + q['text'],
                             answer_lines=3)
                    document['questions'].append(q)
                document['total_marks'] = sum(q['marks'] for q in document['questions'])
                # Instrument only the test HTML. Position a tiny visible marker
                # at the bottom of the FINAL answer line, without adding height
                # or changing the production template's pagination rules.
                html = render_document(document)
                def mark_answer_end(match):
                    article = match.group(0)
                    qid = re.search(r'data-qid="case(\d+)"', article).group(1)
                    line = '<div class="answer-line"></div>'
                    last = article.rfind(line)
                    self.assertGreaterEqual(last, 0)
                    marker = ('<div class="answer-line" style="position:relative">'
                              '<span style="position:absolute;bottom:0;left:0;'
                              'font:1pt Arial;line-height:1;direction:ltr">'
                              f'ANSWER_END_{qid}</span></div>')
                    return article[:last] + article[last:].replace(line, marker, 1)
                html, count = re.subn(r'<article\b.*?</article>', mark_answer_end,
                                      html, flags=re.DOTALL)
                self.assertEqual(count, 24)
                with patch('app.document_templates.renderer.render_document', return_value=html):
                    data = export_pdf(document)
                with fitz.open(stream=data, filetype='pdf') as pdf:
                    self.assertGreater(len(pdf), 2)
                    texts = [page.get_text() for page in pdf]
                    for n in range(24):
                        starts = [i for i, text in enumerate(texts) if f'BEGIN_{n:02}' in text]
                        ends = [i for i, text in enumerate(texts) if f'ANSWER_END_{n:02}' in text]
                        self.assertEqual(len(starts), 1)
                        self.assertEqual(len(ends), 1)
                        self.assertEqual(starts, ends)
                        start_rect = pdf[starts[0]].search_for(f'BEGIN_{n:02}')[0]
                        end_rect = pdf[ends[0]].search_for(f'ANSWER_END_{n:02}')[0]
                        self.assertGreater(end_rect.y0, start_rect.y1)
                        self.assertLess(end_rect.y1, pdf[ends[0]].rect.height - 14 * 72 / 25.4)

    def test_oversized_card_requires_manual_review(self):
        document = fixture('worksheet.json')
        document['questions'] = [document['questions'][0]]
        document['questions'][0]['text'] = '\n'.join(['نص طويل للاختبار'] * 90)
        document['questions'][0]['table'] = {'headers':['عمود'], 'rows':[['كلمة ' * 30] for _ in range(8)]}
        with self.assertRaisesRegex(ValueError, 'oversized block'):
            export_pdf(document)

    def test_corrupt_png_is_detected_at_render(self):
        document = fixture('worksheet.json')
        document['questions'][-1]['image']['data'] = 'data:image/png;base64,iVBORw0KGgo='
        with self.assertRaisesRegex(ValueError, 'broken image'):
            export_pdf(document)


if __name__ == '__main__':
    unittest.main()
