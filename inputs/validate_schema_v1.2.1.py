"""Validate Synthetic Clinical Records Schema V1.2 and any supplied reference dataset.

Usage:
    python inputs/validate_schema_v1.2.1.py
    python inputs/validate_schema_v1.2.1.py inputs/clinical_records_reference_reviewed_v1.2.1.json

Requires: pip install jsonschema
"""
import copy
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA_FILE = Path(__file__).with_name('clinical_records_schema_v1.2.1.json')


def load_schema():
    with SCHEMA_FILE.open(encoding='utf-8') as f:
        schema = json.load(f)
    Draft202012Validator.check_schema(schema)
    return schema


def age_on_date(dob, encounter):
    born = datetime.strptime(dob, '%d/%m/%Y').date()
    seen = datetime.strptime(encounter, '%d/%m/%Y').date()
    if seen < born:
        raise ValueError('Encounter date precedes birthdate')
    return seen.year-born.year-((seen.month,seen.day)<(born.month,born.day))


def check_semantics(data):
    """Additional deterministic checks not expressible in ordinary JSON Schema."""
    problems = []
    for record_id, patient in data.items():
        p=patient['patient_information']
        try:
            expected=age_on_date(p['date_of_birth'],p['encounter_date'])
            if p['age'] != expected:
                problems.append(f'{record_id}: age {p["age"]} differs from calculated {expected}')
        except (ValueError,TypeError) as exc:
            problems.append(f'{record_id}: date error: {exc}')
        if p['id'] != record_id:
            problems.append(f'{record_id}: inner patient ID mismatch')
        dates = []
        ex=patient['clinical_examination_and_investigations']
        for test in ex['laboratory_tests']:
            dates.append((test.get('ordered_date'),test.get('performed_date'),'test ordered/performed'))
            dates.append((test.get('performed_date'),test.get('result_date'),'test performed/result'))
        for test in ex['imaging_studies']:
            dates.append((test.get('ordered_date'),test.get('performed_date'),'imaging ordered/performed'))
        for medication in patient['medication_management']['medications']:
            dates.append((medication.get('actual_start_date'),medication.get('end_date'),'medication start/end'))
            if medication['lifecycle_status']=='planned' and medication['administration_events']:
                problems.append(f'{record_id}: planned medication has administration events; review status')
            for event in medication['administration_events']:
                if event['status']=='not_given' and not event.get('reason'):
                    problems.append(f'{record_id}: not_given event missing reason')
        for a,b,label in dates:
            if a and b:
                try:
                    if datetime.strptime(a,'%d/%m/%Y') > datetime.strptime(b,'%d/%m/%Y'):
                        problems.append(f'{record_id}: {label} chronology reversed')
                except ValueError as exc:
                    problems.append(f'{record_id}: invalid date in {label}: {exc}')
    return problems


def empty_record(schema, rid):
    """Construct an IN-MEMORY structural fixture; not a clinical patient record."""
    defs = schema['$defs']
    def build(name):
        properties = defs[name]['properties']
        result = {}
        for key, spec in properties.items():
            if '$ref' in spec:
                target=spec['$ref'].split('/')[-1]
                result[key]=build(target) if defs[target].get('type') == 'object' else None
            elif spec.get('type')=='array':result[key]=[]
            elif spec.get('type')=='object':result[key]={}
            elif 'anyOf' in spec:result[key]=None
            elif spec.get('type')=='integer':result[key]=0
            elif isinstance(spec.get('type'),list):result[key]=None
            elif spec.get('type')=='string':result[key]='TEST'
            else:result[key]=None
        return result
    rec=build('patient_record')
    rec['patient_information'].update({
        'id':rid,'full_name':'Synthetic Test Name','fictional_identification_number':'SYN-ID-'+rid[-4:],
        'date_of_birth':'15/04/1985','age':41,'sex':'Female','encounter_date':'20/10/2026',
        'phone_number':'SYN-PHONE-'+rid[-4:],'email':f'test{rid[-4:]}@example.invalid','address':'Fictional test address'
    })
    rec['reason_for_consultation']['encounter_type']=None
    rec['medical_glossary']['glossary_terms']=[
        {'term':'Term 1','definition_en':'Example definition','definition_es':'Definición de ejemplo'},
        {'term':'Term 2','definition_en':'Another definition','definition_es':'Otra definición'}]
    return rec


def test_schema(schema):
    validator=Draft202012Validator(schema)
    fixture={f'HC{i:04d}':empty_record(schema,f'HC{i:04d}') for i in range(1,11)}
    tests=[]
    def check(name,mutate,expected_valid=False):
        case=copy.deepcopy(fixture)
        mutate(case)
        errors=list(validator.iter_errors(case))
        valid=not errors
        passed=valid == expected_valid
        tests.append((name,passed,'accepted' if valid else 'rejected'))
    check('Valid synthetic structural fixture',lambda d:None, True)
    check('Missing section',lambda d:d['HC0001'].pop('clinical_progress'))
    check('Unexpected section',lambda d:d['HC0001'].__setitem__('extra_section',{}))
    check('Unexpected demographic field',lambda d:d['HC0001']['patient_information'].__setitem__('nickname','X'))
    check('Missing one patient',lambda d:d.pop('HC0010'))
    check('Incorrect nested ID',lambda d:d['HC0001']['patient_information'].__setitem__('id','HC0002'))
    check('Invalid age type',lambda d:d['HC0001']['patient_information'].__setitem__('age','forty-one'))
    check('Malformed date pattern',lambda d:d['HC0001']['patient_information'].__setitem__('date_of_birth','1985-04-15'))
    check('Invalid glossary count',lambda d:d['HC0001']['medical_glossary'].__setitem__('glossary_terms',[]))
    check('Invalid warning urgency',lambda d:d['HC0001']['warnings_and_red_flags']['warning_signs'].append({'sign':'Test','urgency':'critical','recommended_action':None}))
    def wrong_med(d):
        med=empty_med(schema)
        med['lifecycle_status']='inactive'
        d['HC0001']['medication_management']['medications'].append(med)
    check('Invalid medication lifecycle',wrong_med)
    def wrong_clock(d):
        med=empty_med(schema)
        med['dose_instruction']['fixed_times']=['25:61']
        d['HC0001']['medication_management']['medications'].append(med)
    check('Invalid medication clock time',wrong_clock)
    def wrong_status(d):
        d['HC0001']['follow_up_and_monitoring_plan']['follow_up_items'].append({'type':'appointment','specialty':None,'interval':None,'scheduled_date':None,'purpose':None,'status':'booked'})
    check('Invalid follow-up status',wrong_status)
    semantic_tests=[]
    valid_semantics=check_semantics(fixture)
    semantic_tests.append(('Valid fixture age check', not valid_semantics))
    tampered=copy.deepcopy(fixture)
    tampered['HC0001']['patient_information']['age']=42
    semantic_tests.append(('Incorrect age detected',bool(check_semantics(tampered))))
    return tests,semantic_tests


def empty_med(schema):
    d={'name':'Example medication','strength':None,'route':None,'dose':None,'frequency':None,
       'duration':None,'dose_instruction':{'time_of_day':[],'meal_relation':[],
       'fixed_times':[],'additional_instructions':None},'indication':None,
       'lifecycle_status':'planned','planned_start_date':None,'actual_start_date':None,
       'end_date':None,'status_reason':None,'administration_events':[]}
    return d


def main():
    schema=load_schema()
    tests,semantic_tests=test_schema(schema)
    print('Draft 2020-12 schema: VALID')
    for name,ok,outcome in tests:
        print(f'{"PASS" if ok else "FAIL"}: {name} ({outcome})')
    for name,ok in semantic_tests:
        print(f'{"PASS" if ok else "FAIL"}: {name}')
    if len(sys.argv)>1:
        with open(sys.argv[1],encoding='utf-8') as f:
            dataset=json.load(f)
        errs=list(Draft202012Validator(schema).iter_errors(dataset))
        print(f'Uploaded reference structural errors: {len(errs)}')
        for err in errs[:30]:print(' -', '/'.join(map(str,err.absolute_path)),err.message)
        if not errs:
            notes=check_semantics(dataset)
            print(f'Additional deterministic issues: {len(notes)}')
            for note in notes:print(' -',note)
        if errs:sys.exit(1)
    if not all(ok for _,ok,_ in tests) or not all(ok for _,ok in semantic_tests):sys.exit(1)

if __name__=='__main__':main()
