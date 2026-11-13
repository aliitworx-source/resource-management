from pathlib import Path
from playwright.sync_api import sync_playwright

root=Path(__file__).resolve().parents[1]
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1440,'height':1050},device_scale_factor=1)
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://127.0.0.1:3000')
    page.get_by_label('Username',exact=True).fill('admin')
    page.get_by_label('Password',exact=True).fill('local-demo-only')
    page.get_by_role('button',name='Sign in').click()
    page.get_by_role('heading',name='Resource planner',exact=True).wait_for()
    assert page.locator('.planner tbody tr').count()==6
    page.screenshot(path=str(root/'.local/planner.png'),full_page=True)
    page.get_by_role('button',name='Month',exact=True).click()
    assert page.locator('.month-day').count()>=28
    page.get_by_role('button',name='Week',exact=True).click()
    page.get_by_role('button',name='New allocation').click()
    page.get_by_label('Task / allocation name').fill('Browser shared allocation')
    page.locator('select[name=project_id]').select_option('p1')
    page.locator('input[name=people][value=r1]').check()
    page.locator('input[name=people][value=r2]').check()
    page.locator('select[name=unit]').select_option('week')
    page.locator('input[name=hours]').fill('25')
    page.get_by_role('button',name='Save allocation').click()
    page.wait_for_selector('dialog',state='hidden')
    assert page.get_by_role('button',name='Browser shared allocation').count()==10
    assert page.evaluate("planned(D.resources.find(r=>r.id==='r1'),start,false)")==13
    page.get_by_role('button',name='Browser shared allocation').first.click()
    assert page.locator('input[name=people]:checked').count()==2
    page.get_by_role('button',name='Delete',exact=True).click()
    page.get_by_role('button',name='Delete allocation',exact=True).click()
    page.wait_for_selector('dialog',state='hidden')
    for nav in ['Overview','Resources','Projects','Timesheets','Import center','Reconciliation','Reports','Audit history','Access & users']:
        page.locator('nav').get_by_role('button',name=nav).click()
        assert page.locator('#main h1').is_visible()
    page.locator('nav').get_by_role('button',name='Resource planner').click()
    page.get_by_label('Search people or tasks',exact=True).fill('Jamie')
    assert page.locator('.planner tbody tr').count()==1
    page.get_by_label('Search people or tasks',exact=True).fill('')
    page.set_viewport_size({'width':390,'height':844})
    page.screenshot(path=str(root/'.local/mobile.png'),full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    assert not errors,errors
    browser.close()
    print('Browser checks passed: login, week/month, shared allocation create/edit/delete, all navigation, search, mobile layout; no JS errors.')
