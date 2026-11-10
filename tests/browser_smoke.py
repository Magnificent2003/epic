"""Optional real-browser regression checks.

Run with Playwright and its Chromium headless shell installed:
    python3 tests/browser_smoke.py
    python3 tests/browser_smoke.py --screenshots /tmp/epic-preview

Uses only synthetic answers and temporary storage. This is not a runtime dependency.
"""
from pathlib import Path
import sys, tempfile, threading, json, argparse
from wsgiref.simple_server import make_server, WSGIRequestHandler
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.main import create_app
try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:
    raise SystemExit('Optional browser test requires Playwright and Chromium: pip install playwright; python3 -m playwright install chromium --only-shell')

parser=argparse.ArgumentParser(description='Optional EPIC browser regression checks')
parser.add_argument('--screenshots',type=Path,help='Keep screenshots in this directory; omitted screenshots are temporary')
args=parser.parse_args()

class Quiet(WSGIRequestHandler):
    def log_message(self, *args): pass

checks=[]
def ok(name):
    checks.append(name); print('PASS', name, flush=True)

def field(page, ref): return page.locator(f'[data-field="{ref}"]')
def option(page,ref,code): return page.locator(f'input[name="{ref}"][value="{code}"]')
def choose(page,ref,code): option(page,ref,code).check()
def fill(page,ref,text): page.locator(f'[name="{ref}"]').fill(text)
def stage(page, n): page.locator('.step-button').nth(n).click()
def overflow(page): return page.evaluate('document.documentElement.scrollWidth > innerWidth')

with tempfile.TemporaryDirectory(prefix='epic-browser-answers-') as data:
    screenshot_dir=args.screenshots or Path(data)/'screenshots'
    screenshot_dir.mkdir(parents=True,exist_ok=True)
    server=make_server('127.0.0.1',0,create_app(storage_root=data,auto_reports=False),handler_class=Quiet)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=['--no-sandbox','--no-proxy-server'])
            context=browser.new_context(viewport={'width':1440,'height':1000},device_scale_factor=1,accept_downloads=True)
            page=context.new_page();page.set_default_timeout(8000)
            errors=[]; page.on('pageerror',lambda error: errors.append(str(error)))
            failed=[False]
            def fail_first(route):
                if not failed[0]: failed[0]=True;route.abort()
                else: route.continue_()
            page.route('**/api/questionnaire',fail_first)
            page.goto(base)
            expect(page.get_by_role('heading',name='问卷暂时没有加载成功')).to_be_visible()
            page.get_by_role('button',name='重新加载',exact=True).click()
            expect(page.get_by_role('heading',name='从了解自己开始')).to_be_visible()
            assert page.locator('.step-button').nth(1).is_disabled()
            ok('definition load failure and retry; consent gates section navigation')
            choose(page,'consent.report_use','disagree');page.locator('#next-step').click()
            expect(page.get_by_role('heading',name='本次填写已结束')).to_be_visible()
            assert not list(Path(data).glob('*/v*/answers.json'))
            page.get_by_role('button',name='重新开始',exact=True).click()
            choose(page,'consent.report_use','agree');choose(page,'consent.private_completion','uncomfortable');page.locator('#next-step').click()
            expect(page.get_by_role('heading',name='先停一会儿，也没关系')).to_be_visible()
            assert page.locator('fieldset').count()==0
            page.get_by_role('button',name='继续填写',exact=True).click()
            assert not option(page,'consent.private_completion','uncomfortable').is_checked()
            choose(page,'consent.private_completion','comfortable');page.locator('#next-step').click()
            expect(page.get_by_role('heading',name='先了解你的基本情况')).to_be_visible()
            ok('disagreement stops; private-environment pause hides answers and rechecks environment')
            fill(page,'q01.age_years','28')
            fill(page,'q02.last_menstrual_period','2026-09-20')
            fill(page,'q02.gestational_weeks','7');fill(page,'q02.gestational_days','3')
            choose(page,'q03.pregnancy_status','suspected_or_confirmed_ectopic')
            expect(page.locator('.medical-note')).to_be_visible()
            choose(page,'q03.pregnancy_status','unknown')
            fill(page,'q05.pregnancies_total','2');fill(page,'q05.deliveries_count','1')
            fill(page,'q05.pregnancies_total','1')
            assert field(page,'q05.deliveries_count').count()==0
            assert field(page,'q05.living_children_band').count()==1
            choose(page,'q04.partner_status','fixed_partner')
            ok('date/integer input; ectopic guidance; first-pregnancy dependent fields clear')
            stage(page,6)
            choose(page,'q29.partner_attitude','continue');fill(page,'q29.partner_support_notes','仅用于测试的补充')
            choose(page,'q30.valued_opinions','self');choose(page,'q30.valued_opinions','no_others')
            assert option(page,'q30.valued_opinions','self').is_checked()
            assert option(page,'q30.valued_opinions','no_others').is_checked()
            choose(page,'q30.valued_opinions','partner')
            assert option(page,'q30.valued_opinions','self').is_checked()
            assert not option(page,'q30.valued_opinions','no_others').is_checked()
            stage(page,1);choose(page,'q04.partner_status','no_partner');stage(page,6)
            assert field(page,'q29.partner_attitude').count()==0
            expect(page.locator('[data-question="q29"]')).to_contain_text('这一题无需填写')
            ok('no-partner hides whole question and clears prior support; q30 mutual-exclusion exception')
            stage(page,2)
            assert field(page,'q09.continuation_parenting_affordability').count()==1
            assert field(page,'q09.termination_recovery_affordability').count()==1
            choose(page,'q11.main_activity','family_care')
            assert field(page,'q11.work_school_support').count()==0
            choose(page,'q11.main_activity','other')
            field(page,'q11.work_school_support').get_by_role('button',name='不适用',exact=True).click()
            expect(field(page,'q11.work_school_support')).to_contain_text('已选择不适用')
            ok('paired costs and work/school applicability')
            stage(page,3)
            choose(page,'q15.continuation_willingness','conditional');fill(page,'q15.continuation_conditions','需要照护支持')
            choose(page,'q15.continuation_willingness','willing')
            assert field(page,'q15.continuation_conditions').count()==0
            choose(page,'q16.termination_acceptability','conditional');fill(page,'q16.termination_conditions','需要先获得解释')
            choose(page,'q16.termination_acceptability','acceptable')
            assert field(page,'q16.termination_conditions').count()==0
            for factor in ['健康信息','生活安排']:
                field(page,'q17.value_priorities').get_by_role('button',name='+ 添加一项重要考虑',exact=True).click()
                position=field(page,'q17.value_priorities').locator('.rank-item').count()-1
                page.locator(f'#field-q17-value_priorities-{position}-factor').fill(factor)
            page.get_by_role('button',name='将第2项上移',exact=True).click()
            assert page.locator('#field-q17-value_priorities-0-factor').input_value()=='生活安排'
            field(page,'q17.value_priorities').get_by_role('button',name='+ 添加一项重要考虑',exact=True).click()
            stage(page,7)
            expect(field(page,'q17.value_priorities').locator('.field-error')).to_be_visible()
            page.get_by_role('button',name='删除第3项',exact=True).click()
            ok('conditional followups clear; explicit ranking reorders; incomplete ranking blocks navigation')
            stage(page,7)
            for code in ['confirm_pregnancy','emotional_support','care_support']:
                choose(page,'q37.support_priorities',code)
            option(page,'q37.support_priorities','financial_policy').click()
            assert field(page,'q37.support_priorities').locator('input:checked').count()==3
            expect(page.locator('.toast')).to_contain_text('最多选择3项')
            choose(page,'q37.support_priorities','none')
            assert field(page,'q37.support_priorities').locator('input:checked').count()==1
            choose(page,'q37.support_priorities','emotional_support')
            assert not option(page,'q37.support_priorities','none').is_checked()
            fill(page,'q35.decision_confidence','5.5');page.locator('#next-step').click()
            expect(field(page,'q35.decision_confidence').locator('.field-error')).to_be_visible()
            fill(page,'q35.decision_confidence','7')
            page.get_by_role('button',name='暂时离开',exact=True).click()
            assert page.locator('fieldset').count()==0
            page.get_by_role('button',name='继续填写',exact=True).click()
            assert page.locator('[name="q35.decision_confidence"]').input_value()=='7'
            ok('max-three support needs and exclusivity; invalid integer retained; pause resumes data')
            with page.expect_download() as downloaded:
                page.get_by_role('button',name='保存草稿到本机',exact=True).click()
            draft=json.loads(Path(downloaded.value.path()).read_text())
            assert draft['payload']['answers']['q38']['report_action']['status']=='unanswered'
            page.locator('#next-step').click()
            expect(page.get_by_role('heading',name='给自己的回答，再看一眼')).to_be_visible()
            page.locator('#exclude-group-q15 summary').click();page.locator('#exclude-q15-continuation_willingness').check()
            fill(page,'q38.report_notes','仅用于浏览器验证')
            choose(page,'q38.report_action','confirm')
            stage(page,1);fill(page,'q01.age_years','29');stage(page,8)
            assert not option(page,'q38.report_action','confirm').is_checked()
            page.locator('#next-step').click()
            expect(field(page,'q38.report_action').locator('.field-error')).to_be_visible()
            choose(page,'q38.report_action','confirm')
            failure=[False]
            def submission_fail_once(route):
                if not failure[0]: failure[0]=True; route.abort()
                else: route.continue_()
            page.route('**/api/submissions',submission_fail_once)
            page.locator('#next-step').click()
            expect(page.locator('.error-banner')).to_contain_text('暂时无法连接')
            assert option(page,'q38.report_action','confirm').is_checked()
            page.locator('#next-step').click()
            expect(page.get_by_role('heading',name='答卷已提交')).to_be_visible()
            files=list(Path(data).glob('*/v1/answers.json'));assert len(files)==1
            record=json.loads(files[0].read_text())
            assert record['answers']['q05']['deliveries_count']['status']=='skipped'
            assert record['answers']['q29']['partner_support_notes']['status']=='skipped'
            assert record['answers']['q15']['continuation_conditions']['status']=='skipped'
            assert record['answers']['q17']['value_priorities']['value'][0]['factor']=='生活安排'
            assert record['answers']['q38']['excluded_report_fields']['value']==['q15.continuation_willingness']
            assert record['answers']['q01']['age_years']['value']==29
            ok('draft export; review exclusions; edits invalidate confirmation; failed submission retries without data loss')
            page.get_by_role('button',name='返回修改',exact=True).click()
            assert not option(page,'q38.report_action','confirm').is_checked()
            choose(page,'q38.report_action','confirm');page.locator('#next-step').click()
            expect(page.get_by_role('heading',name='答卷已提交')).to_be_visible()
            assert len(list(Path(data).glob('*/v2/answers.json')))==1
            assert json.loads(files[0].read_text())==record
            ok('resubmission creates immutable version 2')
            page.get_by_role('button',name='返回修改',exact=True).click();stage(page,3)
            page.screenshot(path=str(screenshot_dir/'desktop.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            assert not overflow(page)
            page.screenshot(path=str(screenshot_dir/'mobile.png'),full_page=True)
            page.locator('[aria-label="跳转填写阶段"]').select_option('8')
            assert not overflow(page)
            page.screenshot(path=str(screenshot_dir/'review-mobile.png'),full_page=True)
            assert not errors,errors
            ok('mobile layout at 390px, full review, and no uncaught JS errors')
            # New session imports exported draft and must re-confirm both gates.
            second=context.new_page();second.goto(base,wait_until='networkidle')
            with second.expect_file_chooser() as chooser:
                second.get_by_role('button',name='导入一份草稿',exact=True).click()
            chooser.value.set_files({'name':'draft.json','mimeType':'application/json','buffer':json.dumps(draft).encode()})
            expect(second.locator('.toast')).to_contain_text('草稿已恢复')
            assert second.locator('input[name="consent.report_use"]:checked').count()==0
            assert second.locator('input[name="consent.private_completion"]:checked').count()==0
            choose(second,'consent.report_use','agree');choose(second,'consent.private_completion','comfortable');second.locator('#next-step').click()
            assert second.locator('[name="q01.age_years"]').input_value()=='28'
            ok('draft import restores business answers and requires renewed consent/environment')
            def compose(target_page,selector,text):
                target_page.locator(selector).scroll_into_view_if_needed()
                same=target_page.evaluate('''({selector,text}) => {
                    const node=document.querySelector(selector);
                    node.focus();
                    node.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true,data:''}));
                    node.value=text;
                    node.dispatchEvent(new InputEvent('input',{bubbles:true,data:text,isComposing:true,inputType:'insertCompositionText'}));
                    const stable=node.isConnected && document.querySelector(selector)===node;
                    node.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true,data:text}));
                    return stable;
                }''',{'selector':selector,'text':text})
                assert same, 'IME input node replaced during composition'
                assert target_page.locator(selector).input_value()==text
            compose(second,'[name="q07.contraception_notes"]','中文输入测试')
            stage(second,3)
            compose(second,'#field-q17-value_priorities-0-factor','生活安排的中文输入')
            ok('Chinese IME composition preserves ordinary and ranked input nodes until compositionend')
            stage(second,8)
            second.locator('#exclude-group-q15 summary').click()
            stable_scroll=second.evaluate('''() => {
                const wrap=document.querySelector('.exclusions');
                wrap.scrollTop=300;
                const before=wrap.scrollTop;
                document.querySelector('#exclude-q15-continuation_willingness').click();
                return Math.abs(document.querySelector('.exclusions').scrollTop-before)<2;
            }''')
            assert stable_scroll
            choose(second,'q38.report_action','confirm')
            second.route('**/api/submissions',lambda route:route.fulfill(status=422,content_type='application/json',body=json.dumps({'valid':False,'errors':[{'field':'q01.age_years','code':'type','message':'请再次核对年龄'}]})))
            second.locator('#next-step').click()
            expect(second.get_by_role('heading',name='先了解你的基本情况')).to_be_visible()
            expect(field(second,'q01.age_years').locator('.field-error')).to_contain_text('请再次核对年龄')
            expect(second.locator('[name="q01.age_years"]')).to_be_focused()
            assert second.locator('[name="q01.age_years"]').input_value()=='28'
            ok('exclusion list keeps internal scroll; backend 422 navigates and focuses correct field')
            native=context.new_page()
            native.set_content('<input type="date" id="date"><input type="month" id="month">')
            fill(second,'q05.pregnancies_total','2')
            for kind,ref,keys in [
                ('date','q02.last_menstrual_period',['ArrowUp','ArrowRight','ArrowUp','ArrowRight','ArrowUp','ArrowUp']),
                ('month','q05.last_pregnancy_end_month',['ArrowUp','ArrowRight','ArrowUp','ArrowUp']),
            ]:
                values=[]
                for target in [native.locator('#'+kind),second.locator(f'[name="{ref}"]')]:
                    target.fill('');target.focus();target.press('ArrowLeft');target.press('ArrowLeft')
                    for key in keys:target.press(key)
                    values.append(target.input_value())
                assert values[0] and values[0]==values[1],(kind,values)
            fill(second,'q01.age_years','')
            second.locator('[name="q01.age_years"]').press_sequentially('28')
            assert second.locator('[name="q01.age_years"]').input_value()=='28'
            ok('date and month keyboard segments retain native focus; multi-digit integer entry works')
            date_input=second.locator('[name="q02.last_menstrual_period"]')
            date_input.focus();date_input.press('ArrowRight');date_input.press('ArrowRight');date_input.press('Backspace')
            assert date_input.evaluate('(node)=>node.validity.badInput')
            stage(second,2)
            expect(field(second,'q02.last_menstrual_period').locator('.field-error')).to_be_visible()
            date_input.fill('')
            stage(second,2)
            expect(second.get_by_role('heading',name='看看身边可用的资源')).to_be_visible()
            ok('incomplete native date blocks navigation; deliberate clearing allows continuation')
            browser.close()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
print('TOTAL',len(checks),'scenario groups passed')
