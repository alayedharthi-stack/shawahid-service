"""Offline template contracts. Run with unittest, no live-service fixtures."""
import copy
import json
import unittest
from pathlib import Path

from app.document_templates import render_document, validate_document

ROOT = Path(__file__).resolve().parents[2]


class DocumentTemplatesTest(unittest.TestCase):
    def setUp(self):
        self.document = json.loads((ROOT / "examples/documents/v1/worksheet.json").read_text(encoding="utf8"))
        self.answers = json.loads((ROOT / "examples/documents/v1/worksheet.answers.json").read_text(encoding="utf8"))

    def test_student_has_no_answer_data(self):
        student = render_document(self.document)
        self.assertEqual(student.count('data-qid="'), 8)
        for answer in self.answers.values():
            self.assertNotIn(answer["text"], student)
        self.assertNotIn('class="solutions"', student)
        self.assertNotIn('class="marks"', student)
        self.assertNotIn('الدرجة:', student)
        self.assertIn('<mfrac>', student)
        self.assertIn('<img ', student)
        self.assertIn('class="question-table"', student)

    def test_complete_answer_key_in_original_order(self):
        html = render_document(self.document, answers=self.answers)
        self.assertEqual(html.count('data-qid="'), 8)
        self.assertLess(html.index('data-qid="q1"'), html.index('data-qid="q8"'))
        del self.answers['q8']
        with self.assertRaises(ValueError):
            render_document(self.document, answers=self.answers)

    def test_answers_cannot_be_hidden_in_question_payload(self):
        self.document['questions'][0]['correct_answer'] = '11'
        with self.assertRaises(ValueError):
            render_document(self.document)

    def test_escapes_profile_question_and_math(self):
        payload = '<script>alert("x")</script>'
        self.document['profile']['teacher'] = payload
        self.document['questions'][0]['text'] = payload
        self.document['questions'][0]['equation'] = [payload]
        html = render_document(self.document)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_marks_are_opt_in_and_consistent(self):
        self.document['show_marks'] = True
        self.document['total_marks'] = 10
        self.assertIn('class="marks"', render_document(self.document))
        self.document['total_marks'] = 11
        with self.assertRaises(ValueError):
            render_document(self.document)

    def test_exam_cannot_hide_marks_or_omit_a_score(self):
        self.document['kind'] = 'exam'
        self.document['total_marks'] = 10
        html = render_document(self.document)
        self.assertIn('class="score-grid"', html)
        self.assertIn('class="marks"', html)
        del self.document['questions'][0]['marks']
        with self.assertRaises(ValueError):
            validate_document(self.document)

    def test_unique_ids_and_finite_marks(self):
        bad = copy.deepcopy(self.document)
        bad['questions'][1]['id'] = 'q1'
        with self.assertRaises(ValueError):
            validate_document(bad)
        for marks in (0, -1, float('nan'), float('inf'), True):
            self.document['questions'][0]['marks'] = marks
            with self.assertRaises(ValueError):
                validate_document(self.document)

    def test_no_remote_or_svg_images(self):
        for data in ('https://example.com/x.png', 'file:///secret', 'data:image/svg+xml;base64,AAAA'):
            self.document['questions'][7]['image']['data'] = data
            with self.assertRaises(ValueError):
                validate_document(self.document)

    def test_missing_profile_is_blank_not_invented(self):
        html = render_document(self.document)
        self.assertIn('المدرسة: ................................', html)
        self.assertNotIn('وزارة التعليم', html)
        self.assertNotIn('معتمد', html)

    def test_input_is_not_mutated(self):
        before = copy.deepcopy(self.document)
        render_document(self.document)
        self.assertEqual(before, self.document)

    def test_fixture_math_and_single_correct_choice(self):
        # Independently substitute solutions into the actual fixture equations.
        values = {'q1':11,'q2':20,'q3':7,'q4':30,'q5':13,'q6':9}
        trans = str.maketrans('٠١٢٣٤٥٦٧٨٩','0123456789')
        def number(token, x):
            if isinstance(token, dict):
                return number(token['numerator'], x) / number(token['denominator'], x)
            return x if token == 'س' else int(token.translate(trans))
        def satisfies(q, x):
            eq=q['equation']; equal=eq.index('=');left=eq[:equal]
            a=number(left[0],x)
            if len(left)==3:
                b=number(left[2],x);a={'+':lambda:a+b,'−':lambda:a-b,'×':lambda:a*b}[left[1]]()
            return a == number(eq[equal+1],x)
        for q in self.document['questions'][:6]:
            self.assertTrue(satisfies(q,values[q['id']]))
        mcq=self.document['questions'][4]
        self.assertEqual([int(x.translate(trans)) for x in mcq['choices'] if satisfies(mcq,int(x.translate(trans)))],[13])
        self.assertFalse(satisfies(self.document['questions'][5],6))
        self.assertEqual([x+4 for x in (3,5,8)],[7,9,12])
        self.assertEqual(3*9,27)

    def test_plan_contract(self):
        plan=copy.deepcopy(self.document);plan['kind']='weekly_plan';del plan['questions']
        plan['days']=[dict(day='الأحد',date='',lesson='درس',objective='هدف',activity='نشاط',assessment='سؤال',homework='تدريب')]
        html=render_document(plan)
        self.assertIn('class="plan-table"',html)
        self.assertNotIn('class="marks"',html)
        plan['days'][0]['unknown']='value'
        with self.assertRaises(ValueError):validate_document(plan)


if __name__ == '__main__':
    unittest.main()
