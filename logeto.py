#!/usr/bin/env python3
"""Logeto CLI: an unofficial terminal client for Logeto / Výkaz práce timesheets.

Not affiliated with or endorsed by Systemart s.r.o. "Logeto" and "Výkaz práce" are their product names.

The timesheet page is ASP.NET WebForms on DevExpress 18.1. Without an API key there is no JSON API for
records, so this tool sends the same postbacks and grid callbacks as the browser. See README.md.
"""
import argparse
import collections
import datetime as dt
import getpass
import html
import http.cookiejar
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

__version__ = '0.2.0'

CONFIG_DIR = os.path.expanduser(os.environ.get('LOGETO_HOME', '~/.config/logeto'))
CONFIG_FILE = os.path.join(CONFIG_DIR, 'config.json')
COOKIE_FILE = os.path.join(CONFIG_DIR, 'cookies.lwp')
CATALOG_FILE = os.path.join(CONFIG_DIR, 'catalog.json')
KEYCHAIN_SERVICE = 'logeto-cli'
DEFAULTS = {'base': None, 'lang': 'cs', 'account': None, 'email': None, 'date_format': 'dd.MM.yyyy',
            'contract': None, 'activity': None}
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36'

GRID = 'ctl00$DC$GridWebPart1$GridWebPart'
GRID_ID = 'ctl00_DC_GridWebPart1_GridWebPart'
FORM = GRID + '$DXPEForm$efnew$'
FORM_ID = GRID_ID + '_DXPEForm_efnew_'
DATE_RANGE = 'ctl00$DC$GridWebPart1$FilterPanel$DateRange'
TOOLBAR = 'ctl00$DC$GridWebPart1$ToolBar'
CONTRACT_LIST = 'Contract_ComboBox_DDD_L'
ACTIVITY_LIST = FORM_ID + 'ActivityType_DDD_L'
COLUMNS = {'DATUM': 'date', 'DEN': 'day', '_CAS_OD': 'from', '_CAS_DO': 'to', '_POCET_HODIN': 'hours',
           'ACTIVITY_TYPE_CODE_NAME': 'activity', 'POPIS': 'description', 'KODOVY_POPIS_UTVAR': 'contract'}
# Activity time entry mode (ACTIVITY_TYPES.TIME_ENTRY): 0 optional, 1 total hours only, 2 from-to only.
TIME_ENTRY = {'0': 'any', '1': 'hours', '2': 'span'}
# Form fields a company can turn on (AG_*) or make required (RF_*), and whether this tool can fill them.
OPTIONAL_FIELDS = {'ID_UTVARU': ('contract', True), 'ID_POLOZKY_UTVARU': ('subcontract', False),
                   'POPIS': ('description', True), 'DATE': ('date', True),
                   **{f'IDENTIFIKATOR{i}': (f'identifier{i}', False) for i in range(1, 9)}}


class LogetoError(Exception):
    pass


# ---------- config and session ----------

def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding='utf-8') as f:
            cfg.update(json.load(f))
    return cfg


def save_config(cfg):
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def keychain_account(cfg):
    return f"{cfg['email']}@{urllib.parse.urlparse(cfg['base']).hostname}"


def keychain_get(cfg):
    if sys.platform != 'darwin':
        return os.environ.get('LOGETO_PASSWORD')
    r = subprocess.run(['security', 'find-generic-password', '-s', KEYCHAIN_SERVICE, '-a', keychain_account(cfg), '-w'],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else os.environ.get('LOGETO_PASSWORD')


def keychain_set(cfg, password):
    if sys.platform != 'darwin':
        return False
    subprocess.run(['security', 'add-generic-password', '-U', '-s', KEYCHAIN_SERVICE, '-a', keychain_account(cfg),
                    '-w', password], check=True, capture_output=True)
    return True


def normalize_base(url):
    url = url.strip()
    if '://' not in url:
        url = 'https://' + url
    p = urllib.parse.urlparse(url)
    return f'{p.scheme}://{p.netloc}'


class Session:
    def __init__(self, cfg):
        self.cfg = cfg
        os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
        self.jar = http.cookiejar.LWPCookieJar(COOKIE_FILE)
        if os.path.exists(COOKIE_FILE):
            self.jar.load(ignore_discard=True, ignore_expires=True)
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.relogged = False

    @property
    def base(self):
        if not self.cfg.get('base'):
            raise LogetoError('No Logeto address yet. Run `logeto login --url https://<company>.vykazprace.cz`.')
        return self.cfg['base']

    @property
    def timesheet(self):
        return f"/wf/{self.cfg['lang']}/Employees/Timesheet"

    def save(self):
        self.jar.save(ignore_discard=True, ignore_expires=True)
        os.chmod(COOKIE_FILE, 0o600)

    def request(self, path_or_url, fields=None, json_body=None):
        url = path_or_url if path_or_url.startswith('http') else self.base + path_or_url
        h = {'User-Agent': UA, 'Referer': url}
        data = None
        if fields is not None:
            data = urllib.parse.urlencode(fields).encode()
            h['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8'
            h['Origin'] = self.base
        elif json_body is not None:
            data = json.dumps(json_body).encode()
            h['Content-Type'] = 'application/json; charset=utf-8'
        with self.opener.open(urllib.request.Request(url, data=data, headers=h), timeout=60) as r:
            body = r.read().decode('utf-8', 'replace')
            final = r.geturl()
        self.save()
        return final, body

    def page(self, path=None):
        """GET a WebForms page. Log in again once if the session has expired."""
        path = path or self.timesheet
        final, body = self.request(path)
        if is_login(final, body):
            if self.relogged or not login(self, quiet=True):
                raise LogetoError('Session expired. Run `logeto login`.')
            self.relogged = True
            final, body = self.request(path)
        if 'aspnetForm' not in body:
            raise LogetoError(f'{path} is not the timesheet page. Check `logeto config base` and `lang`.')
        return final, body

    def clear(self):
        self.jar.clear()
        self.save()

    def import_cookies(self, header):
        host = urllib.parse.urlparse(self.base).hostname
        for part in header.split(';'):
            if '=' not in part:
                continue
            name, value = part.strip().split('=', 1)
            self.jar.set_cookie(http.cookiejar.Cookie(
                0, name, value, None, False, host, False, False, '/', True, True, None, False, None, None, {}))
        self.save()


def is_login(final_url, body):
    return '/Login' in urllib.parse.urlparse(final_url).path or 'id="loginForm"' in body


def server_constants(body):
    i = body.find('var ServerConstants = ')
    if i < 0:
        return {}
    block = body[i + len('var ServerConstants = '):body.find('</script>', i)].strip().rstrip(';')
    try:
        return json.loads(block)
    except ValueError:
        return {k: v for k, v in re.findall(r'"(\w+)":"([^"]*)"', block)}


def login(sess, quiet=False, password=None):
    cfg = sess.cfg
    if not cfg.get('email'):
        if quiet:
            return False
        cfg['email'] = input('E-mail: ').strip()
    password = password or keychain_get(cfg)
    if not password:
        if quiet:
            return False
        password = getpass.getpass('Password (not echoed): ')
    final, body = sess.request('/Login')
    if not is_login(final, body):
        # A half-valid session redirects away from the login page. Start a fresh session.
        sess.clear()
        final, body = sess.request('/Login')
    consts = server_constants(body)
    action = re.search(r'<form[^>]*action="([^"]+)"[^>]*id="loginForm"', body) or re.search(r'id="loginForm"[^>]*action="([^"]+)"', body)
    if not action:
        raise LogetoError(f'No login form at {final}. Is this a Logeto address?')
    fields = form_inputs(body)
    if not fields.get('AccountName'):
        if not cfg.get('account'):
            if quiet:
                return False
            cfg['account'] = input('Account (company) name: ').strip()
        fields['AccountName'] = cfg['account']
    fields.update({'Email': cfg['email'], 'Password': password, 'RememberMe': 'true'})
    final, body = sess.request(urllib.parse.urljoin(final, html.unescape(action.group(1))), fields)
    if is_login(final, body):
        if quiet:
            return False
        err = re.search(r'id="errorMessage"[^>]*>([^<]+)<', body)
        raise LogetoError('Login failed' + (f': {html.unescape(err.group(1)).strip()}' if err else '. Check the e-mail and password.'))
    consts = {**consts, **server_constants(body)}
    if consts.get('Language'):
        cfg['lang'] = consts['Language']
    if consts.get('ShortDateFormat'):
        cfg['date_format'] = consts['ShortDateFormat']
    if consts.get('AccountName'):
        cfg['account'] = consts['AccountName']
    final, body = sess.request(sess.timesheet)
    if is_login(final, body):
        if quiet:
            return False
        raise LogetoError('Login worked, but the timesheet page still asks for a login.')
    save_config(cfg)
    if not quiet and keychain_get(cfg) != password:
        keychain_set(cfg, password)
    return True


# ---------- WebForms / DevExpress protocol ----------

def form_inputs(s):
    """Successful controls of an HTML fragment, as the browser would post them."""
    out = {}
    for tag in re.findall(r'<input\b[^>]*>', s):
        name = re.search(r'\bname="([^"]*)"', tag)
        if not name:
            continue
        typ = (re.search(r'\btype="([^"]*)"', tag) or [None, 'text'])[1].lower()
        if typ in ('submit', 'button', 'image', 'file', 'reset'):
            continue
        if typ in ('checkbox', 'radio') and 'checked' not in tag:
            continue
        value = re.search(r'\bvalue="([^"]*)"', tag)
        out[html.unescape(name.group(1))] = html.unescape(value.group(1)) if value else ''
    return out


def unescape_js(s):
    return s.replace('\\"', '"').replace("\\'", "'").replace('\\/', '/')


def grid_props(blob):
    i = blob.find("createControl(ASPxClientGridView,'%s'" % GRID_ID)
    return blob[i:] if i >= 0 else blob


def grid_state(blob, focused='', selection=''):
    props = grid_props(blob)
    st = re.search(r"'stateObject':(\{'focusedRow'.*?'selection':'[^']*'\})", props, re.S).group(1)
    keys = json.loads(re.search(r"'keys':(\[[^\]]*\])", st).group(1).replace("'", '"'))
    return {'focusedRow': focused, 'keys': keys, 'resizingState': '',
            'callbackState': re.search(r"'callbackState':'([^']*)'", st).group(1),
            'groupLevelState': {}, 'scrollState': None, 'selection': selection}


def dx_json(obj):
    return html.escape(json.dumps(obj, separators=(',', ':'), ensure_ascii=False))


def form_action(page_html, page_url):
    action = re.search(r'<form[^>]*action="([^"]+)"', page_html).group(1).replace('&amp;', '&')
    return urllib.parse.urljoin(page_url, action)


def postback(sess, url, fields, target, argument=''):
    f = dict(fields)
    f['__EVENTTARGET'] = target
    f['__EVENTARGUMENT'] = argument
    final, body = sess.request(url, f)
    if is_login(final, body):
        raise LogetoError('Session expired. Run `logeto login`.')
    return final, body


def grid_callback(sess, url, fields, state, *command):
    """ASPxGridView callback. Returns (eventValidation, result text)."""
    def fmt(k, v):
        return f'{k}|{len(v)};{v};'
    f = dict(fields)
    f[GRID] = dx_json(state)
    gb = ''.join(f'{len(a)}|{a}' for a in map(str, command))
    f['__CALLBACKID'] = GRID
    f['__CALLBACKPARAM'] = 'c0:' + fmt('KV', json.dumps(state['keys'], separators=(',', ':'))) + fmt('GB', gb)
    final, body = sess.request(url, f)
    if is_login(final, body):
        raise LogetoError('Session expired. Run `logeto login`.')
    if body.startswith('e'):
        raise LogetoError('Server callback error: ' + body[1:300])
    n, rest = body.split('|', 1)
    n = int(n)
    result = rest[n:]
    err = re.search(r"\{'error':\{'message':'((?:[^'\\]|\\.)*)'", result)
    if err:
        msg = html.unescape(unescape_js(err.group(1)))
        if msg == '_MISSING_OVERLAP_ACTION':
            msg = 'The record overlaps an existing record. Change the time, or fix it in the browser.'
        elif msg == '_LOCKED_OVERLAP':
            msg = 'Locked records exist at this time. The server does not allow a save.'
        raise LogetoError(msg)
    return rest[:n], unescape_js(result)


def hidden_field(props):
    """Serialize an ASPxClientHiddenField state (DevExpress HiddenFieldSerializer)."""
    def atom(code, v):
        return f'{code}|{len(v)}|{v}'
    return '12|#|' + ''.join(f'{k}|{atom(c, v)}' for k, c, v in props) + '#'


def hidden_field_props(blob, client_id):
    """Read the properties of a server-rendered ASPxClientHiddenField (for example webEntities)."""
    i = blob.find("createControl(ASPxClientHiddenField,'%s'" % client_id)
    if i < 0:
        return {}
    block = blob[i:blob.find('</script>', i)]
    out = {}
    for key, value in re.findall(r"'dxp(\w+)':('(?:[^'\\]|\\.)*'|true|false|-?[\d.]+|null)", block):
        if value.startswith("'"):
            value = unescape_js(value[1:-1])
            if value[:1] in '[{':
                try:
                    value = json.loads(value)
                except ValueError:
                    pass
        else:
            value = json.loads(value)
        out[key] = value
    return out


# ---------- dates ----------

def net_to_strftime(fmt):
    return re.sub(r'yyyy|MM|dd|M|d', lambda m: {'yyyy': '%Y', 'MM': '%m', 'dd': '%d', 'M': '%m', 'd': '%d'}[m.group(0)], fmt)


def format_date(cfg, day):
    return day.strftime(net_to_strftime(cfg['date_format']))


def read_date(cfg, s):
    try:
        return dt.datetime.strptime(s, net_to_strftime(cfg['date_format'])).date()
    except ValueError:
        return None


def utc_ms(d):
    return str(int(d.replace(tzinfo=dt.timezone.utc).timestamp() * 1000))


# ---------- records ----------

def parse_rows(cfg, blob):
    props = grid_props(blob)
    keys = json.loads(re.search(r"'keys':(\[[^\]]*\])", props).group(1).replace("'", '"'))
    visible = json.loads(re.search(r"'cpVisibleColumns':(\[[^\]]*\])", props).group(1))
    fields = {int(i): name for i, name in
              re.findall(r"\[(\d+),[^\[\]]*?'([A-Z_0-9]+)'", re.search(r"'columnProp':\[(.*?)\]\]", props, re.S).group(1))}
    rows = []
    for idx, tr in re.findall(r'<tr[^>]*id="%s_DXDataRow(\d+)"[^>]*>(.*?)</tr>' % GRID_ID, blob, re.S):
        cells = [html.unescape(re.sub(r'<[^>]+>', '', c)).replace('\xa0', ' ').strip()
                 for c in re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)]
        row = {'key': keys[int(idx) % len(keys)] if keys else None}
        for col, value in zip(visible, cells):
            name = COLUMNS.get(fields.get(col))
            if name:
                row[name] = value
        d = read_date(cfg, row.get('date', ''))
        if d:
            row['iso_date'] = d.isoformat()
        rows.append(row)
    return rows


def page_count(blob):
    m = re.search(r"'pageCount':(\d+)", grid_props(blob))
    return int(m.group(1)) if m else 1


def month_page(sess, year, month):
    url, page = sess.page()
    fields = form_inputs(page)
    data = hidden_field([('AutoPostBack', 8, '1'), ('RangeType', 18, '7'), ('CustomRangeLimitType', 4, '1y1'),
                         ('Month', 2, str(month)), ('MonthYear', 2, str(year))])
    fields[DATE_RANGE + '$ctl00'] = dx_json({'data': data})
    return postback(sess, form_action(page, url), fields, DATE_RANGE)


def list_month(sess, year, month):
    url, page = month_page(sess, year, month)
    rows = parse_rows(sess.cfg, page)
    pages = page_count(page)
    if pages > 1:
        fields = form_inputs(page)
        state = grid_state(page)
        for p in range(1, pages):
            ev, res = grid_callback(sess, url, fields, state, 'PAGERONCLICK', f'PN{p}')
            fields['__EVENTVALIDATION'] = ev
            state = grid_state(res)
            rows += parse_rows(sess.cfg, res)
    return rows


def list_items(blob, control_id):
    m = re.search(r"createControl\(ASPxClientListBox,'%s'.*?'itemsInfo':\[(.*?)\]\}?," % re.escape(control_id), blob, re.S)
    if not m:
        return []
    return [{'value': v.strip("'"), 'text': t.replace("\\'", "'").strip()}
            for v, t in re.findall(r"\{'value':('[^']*'|-?\d+),'text':'((?:[^'\\]|\\.)*)'", m.group(1))]


def open_add_form(sess):
    url, page = sess.page()
    fields = form_inputs(page)
    ev, res = grid_callback(sess, url, fields, grid_state(page), 'ADDNEWROW')
    fields['__EVENTVALIDATION'] = ev
    return url, page, fields, res


def company_settings(page):
    ent = hidden_field_props(page, 'ctl00_DC_HiddenFieldEntities')
    modes = {a['GUID']: TIME_ENTRY.get(str(a.get('TIME_ENTRY')), 'any') for a in ent.get('ACTIVITY_TYPES', []) or []}
    return ent, modes


def catalog(sess, refresh=False):
    """Contracts and activities offered by the add form. Cached in the config directory."""
    if not refresh and os.path.exists(CATALOG_FILE):
        with open(CATALOG_FILE, encoding='utf-8') as f:
            data = json.load(f)
        if data.get('base') == sess.cfg.get('base'):
            return data
    _, page, _, res = open_add_form(sess)
    _, modes = company_settings(page)
    activities = list_items(res, ACTIVITY_LIST)
    for a in activities:
        a['time'] = modes.get(a['value'], 'any')
    data = {'base': sess.cfg['base'], 'contracts': list_items(res, CONTRACT_LIST), 'activities': activities}
    with open(CATALOG_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def pick(items, query, what):
    """Find an item by exact value, exact text, or a unique substring of the text."""
    q = str(query).strip().lower()
    for it in items:
        if str(it['value']).lower() == q or it['text'].lower() == q:
            return it
    hits = [it for it in items if q in it['text'].lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise LogetoError(f'No {what} matches "{query}". See `logeto {what}s`.')
    raise LogetoError(f'"{query}" matches more than one {what}: ' + '; '.join(h['text'] for h in hits[:8]))


def add_record(sess, day, span, hours, description, contract, activity, dry_run=False):
    """span: (time_from, time_to) or None. hours: minutes when only the total is known."""
    url, page, fields, res = open_add_form(sess)
    ent, modes = company_settings(page)
    form = {k: v for k, v in form_inputs(res).items() if k.startswith((FORM, FORM_ID, 'Contract_', 'Subcontract_'))}
    acts = list_items(res, ACTIVITY_LIST)
    a = pick(acts, activity, 'activity') if activity else (acts[0] if acts else None)
    if not a:
        raise LogetoError('The form has no activities.')
    mode = modes.get(a['value'], 'any')
    if mode == 'span' and not span:
        raise LogetoError(f'"{a["text"]}" needs a start and an end time, for example 9:00-17:00.')
    if mode == 'hours' and span:
        raise LogetoError(f'"{a["text"]}" takes the total hours only, for example 7:30.')
    if span:
        minutes = (span[1].hour * 60 + span[1].minute) - (span[0].hour * 60 + span[0].minute)
        if minutes <= 0:
            raise LogetoError('The end time must be after the start time.')
    else:
        minutes = hours
    if ent.get('RF_POPIS') and not description.strip():
        raise LogetoError('Your company requires a description.')
    jan1 = dt.datetime(day.year, 1, 1)
    form[FORM + 'DateEdit'] = format_date(sess.cfg, day)
    form[FORM + 'DateEdit$State'] = dx_json({'rawValue': utc_ms(dt.datetime(day.year, day.month, day.day))})
    times = [('Hours', minutes // 60, minutes % 60), ('TotalHours', minutes // 60, minutes % 60)]
    if span:
        times += [('TimeFrom', span[0].hour, span[0].minute), ('TimeTo', span[1].hour, span[1].minute)]
    for name, hh, mm in times:
        form[FORM + name] = f'{hh:02d}:{mm:02d}'
        form[FORM + name + '$State'] = dx_json({'rawValue': utc_ms(jan1.replace(hour=hh, minute=mm))})
    form[FORM + 'VPRPopis'] = description
    form[FORM_ID + 'ActivityType_VI'] = a['value']
    form[FORM + 'ActivityType'] = a['text']
    record = {'date': day.isoformat(), 'from': span[0].strftime('%H:%M') if span else '',
              'to': span[1].strftime('%H:%M') if span else '', 'hours': f'{minutes // 60}:{minutes % 60:02d}',
              'activity': a['text'], 'contract': '', 'description': description}
    contracts = list_items(res, CONTRACT_LIST)
    if contracts:
        if contract:
            c = pick(contracts, contract, 'contract')
            form['Contract_ComboBox_VI'] = c['value']
            form[FORM + 'ContractCallbackPanel$Contract$Contract_ComboBox'] = c['text']
            record['contract'] = c['text']
        elif ent.get('RF_ID_UTVARU'):
            raise LogetoError('Your company requires a contract. Use -c, or set a default with `logeto config contract`.')
    if dry_run:
        return record
    fields.update(form)
    grid_callback(sess, url, fields, grid_state(res), 'UPDATEEDIT')
    want = format_date(sess.cfg, day)
    for row in list_month(sess, day.year, day.month):
        if (row.get('date') == want and row.get('description') == description and row.get('hours') == record['hours']
                and (row.get('from') or '') == record['from']):
            record['key'] = row['key']
    if 'key' not in record:
        raise LogetoError('The server accepted the record, but it is not in the list. Check it in the browser.')
    return record


def delete_record(sess, key, year, month):
    url, page = month_page(sess, year, month)
    if page_count(page) > 1:
        raise LogetoError('This month has more than one page of records. Delete this one in the browser.')
    state = grid_state(page)
    if key not in state['keys']:
        raise LogetoError(f'Record {key} is not in {year}-{month:02d}. Use --month.')
    idx = state['keys'].index(key)
    state['focusedRow'] = idx
    state['selection'] = ''.join('T' if i == idx else 'F' for i in range(len(state['keys'])))
    fields = form_inputs(page)
    fields[GRID] = dx_json(state)
    _, after = postback(sess, form_action(page, url), fields, TOOLBAR, 'CLICK:2')
    left = grid_state(after)['keys']
    if key in left:
        raise LogetoError(f'Record {key} is still there. The server did not delete it.')
    if set(state['keys']) - {key} - set(left):
        raise LogetoError('More than one record left the list. Check the month in the browser now.')


# ---------- inspect ----------

def months_back(n):
    d = dt.date.today().replace(day=1)
    out = []
    for _ in range(n):
        out.append((d.year, d.month))
        d = (d - dt.timedelta(days=1)).replace(day=1)
    return out


def inspect(sess, history_months=3):
    """Read the company's form setup and the user's recent records, and suggest defaults."""
    _, page, _, res = open_add_form(sess)
    ent, modes = company_settings(page)
    contracts = list_items(res, CONTRACT_LIST)
    activities = list_items(res, ACTIVITY_LIST)
    for a in activities:
        a['time'] = modes.get(a['value'], 'any')
    fields = []
    for code, (name, supported) in OPTIONAL_FIELDS.items():
        on = code in ('POPIS', 'DATE') or bool(ent.get('AG_' + code))
        required = bool(ent.get('RF_' + code))
        if on or required:
            fields.append({'field': name, 'enabled': on, 'required': required, 'supported': supported})
    rows = []
    for y, m in months_back(history_months):
        rows += list_month(sess, y, m)
    contract_use = collections.Counter(r.get('contract') for r in rows if r.get('contract'))
    activity_use = collections.Counter(r.get('activity') for r in rows if r.get('activity'))
    starts = collections.Counter(r['from'] for r in rows if r.get('from'))
    suggest = {}
    if contract_use:
        name = contract_use.most_common(1)[0][0]
        if any(c['text'] == name for c in contracts):
            suggest['contract'] = name
    if activity_use:
        suggest['activity'] = activity_use.most_common(1)[0][0]
    elif activities:
        suggest['activity'] = activities[0]['text']
    with open(CATALOG_FILE, 'w', encoding='utf-8') as f:
        json.dump({'base': sess.cfg['base'], 'contracts': contracts, 'activities': activities}, f, ensure_ascii=False, indent=2)
    return {
        'base': sess.cfg['base'], 'lang': sess.cfg['lang'], 'account': sess.cfg.get('account'),
        'email': sess.cfg.get('email'), 'date_format': sess.cfg['date_format'],
        'manual_records_allowed': ent.get('SC_TIME_TRACKING_MANUAL_RECORDS_ENABLED', True),
        'fields': fields,
        'unsupported_required': [f['field'] for f in fields if f['required'] and not f['supported']],
        'activities': activities, 'contracts': contracts,
        'history': {'months': history_months, 'records': len(rows),
                    'contracts': contract_use.most_common(5), 'activities': activity_use.most_common(5),
                    'usual_start': starts.most_common(1)[0][0] if starts else None},
        'suggested_defaults': suggest,
        'current_defaults': {'contract': sess.cfg.get('contract'), 'activity': sess.cfg.get('activity')},
    }


# ---------- input parsing ----------

def parse_day(s):
    today = dt.date.today()
    words = {'today': 0, 'dnes': 0, 'tomorrow': 1, 'zitra': 1, 'zítra': 1, 'yesterday': -1, 'vcera': -1, 'včera': -1}
    if s.lower() in words:
        return today + dt.timedelta(days=words[s.lower()])
    for f in ('%Y-%m-%d', '%d.%m.%Y', '%d.%m.%y'):
        try:
            return dt.datetime.strptime(s, f).date()
        except ValueError:
            pass
    m = re.fullmatch(r'(\d{1,2})\.(\d{1,2})\.?', s)
    if m:
        try:
            return dt.date(today.year, int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    raise LogetoError(f'Unknown date "{s}". Use 2026-10-09, 9.10., 9.10.2026, today or yesterday.')


def parse_time(s):
    m = re.fullmatch(r'(\d{1,2})(?:[:.](\d{2}))?', s.strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2) or 0) > 59:
        raise LogetoError(f'Unknown time "{s}". Use 9, 9:30 or 09:30.')
    return dt.time(int(m.group(1)), int(m.group(2) or 0))


def parse_when(s):
    """'9:00-17:30' -> (span, minutes). '7:30' or '7.5h' -> (None, minutes)."""
    if '-' in s:
        a, b = s.split('-', 1)
        return (parse_time(a), parse_time(b)), None
    s = s.strip().lower()
    m = re.fullmatch(r'(\d{1,2}):(\d{2})h?', s)
    n = re.fullmatch(r'(\d{1,2}(?:[.,]\d+)?)h?', s)
    if m:
        minutes = int(m.group(1)) * 60 + int(m.group(2))
    elif n:
        minutes = round(float(n.group(1).replace(',', '.')) * 60)
    else:
        raise LogetoError(f'Unknown time "{s}". Use 9:00-17:30 for a span, or 7:30 or 7.5h for total hours.')
    if not 0 < minutes < 24 * 60:
        raise LogetoError('Total hours must be more than 0 and less than 24.')
    return None, minutes


def parse_month(s):
    today = dt.date.today()
    if not s or s in ('this', 'tento'):
        return today.year, today.month
    if s in ('last', 'minuly', 'minulý'):
        first = today.replace(day=1) - dt.timedelta(days=1)
        return first.year, first.month
    m = re.fullmatch(r'(\d{4})-(\d{1,2})', s)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.fullmatch(r'(\d{1,2})/(\d{4})', s)
    if m:
        return int(m.group(2)), int(m.group(1))
    if re.fullmatch(r'\d{1,2}', s):
        return today.year, int(s)
    raise LogetoError(f'Unknown month "{s}". Use 2026-09, 9, this or last.')


# ---------- output ----------

def hours_to_min(h):
    m = re.fullmatch(r'(\d+):(\d{2})', h or '')
    return int(m.group(1)) * 60 + int(m.group(2)) if m else 0


def print_rows(rows):
    if not rows:
        print('No records.')
        return
    w = min(max(len(r.get('contract', '')) for r in rows), 28)
    total = 0
    for r in rows:
        total += hours_to_min(r.get('hours'))
        span = f"{r.get('from') or '':>5}-{r.get('to') or '':<5}" if r.get('from') else ' ' * 11
        print(f"{r['key']:>7}  {r.get('date', ''):10} {r.get('day', ''):2}  {span} {r.get('hours', ''):>5}  "
              f"{r.get('contract', '')[:w]:<{w}}  {r.get('description', '')}")
    days = len({r.get('date') for r in rows})
    print(f"{'':7}  {len(rows)} records, {days} days, {total // 60}:{total % 60:02d} h")


def print_inspect(info):
    print(f"Logeto: {info['base']}  (account {info['account']}, user {info['email']}, language {info['lang']})")
    if not info['manual_records_allowed']:
        print('  ! Your company does not allow manual records. `logeto add` will fail.')
    print('Form fields:')
    for f in info['fields']:
        flags = ('required' if f['required'] else 'optional') + ('' if f['supported'] else ', not supported by this tool')
        print(f"  {f['field']:12} {flags}")
    for name in info['unsupported_required']:
        print(f'  ! "{name}" is required, and this tool cannot fill it yet. `logeto add` will fail.')
    print(f"Activities: {len(info['activities'])}  Contracts: {len(info['contracts'])}")
    h = info['history']
    print(f"History: {h['records']} records in the last {h['months']} months, usual start {h['usual_start'] or '-'}")
    for name, n in h['contracts'][:3]:
        print(f'  contract  {n:>3}x  {name}')
    for name, n in h['activities'][:3]:
        print(f'  activity  {n:>3}x  {name}')
    print('Suggested defaults: ' + json.dumps(info['suggested_defaults'], ensure_ascii=False))


# ---------- commands ----------

def confirm(question, yes):
    return yes or input(f'{question} [y/N] ').strip().lower() in ('y', 'yes', 'a', 'ano', 't', 'tak')


def cmd_login(sess, args):
    if args.url:
        sess.cfg['base'] = normalize_base(args.url)
    if not sess.cfg.get('base'):
        sess.cfg['base'] = normalize_base(input('Logeto address (for example https://company.vykazprace.cz): '))
    if args.email:
        sess.cfg['email'] = args.email
    if args.account:
        sess.cfg['account'] = args.account
    password = sys.stdin.readline().rstrip('\n') if args.password_stdin else None
    sess.clear()
    login(sess, password=password)
    where = 'the macOS Keychain' if sys.platform == 'darwin' else 'nowhere (set LOGETO_PASSWORD for automatic re-login)'
    print(f"Logged in to {sess.cfg['base']} as {sess.cfg['email']}. Password stored in {where}.")


def logout(sess):
    """End the session on the server (as the web "log out" link does) and drop the local cookies."""
    final, body = sess.request(f"/{sess.cfg['lang']}/Login?Logout=1")
    wf = server_constants(body).get('WebFormsApplicationUrl') or sess.base + '/wf'
    sess.request(wf.rstrip('/') + '/LoginService', {'logout': '1'})
    sess.clear()


def keychain_delete(cfg):
    if sys.platform != 'darwin':
        return False
    r = subprocess.run(['security', 'delete-generic-password', '-s', KEYCHAIN_SERVICE, '-a', keychain_account(cfg)],
                       capture_output=True)
    return r.returncode == 0


def cmd_logout(sess, args):
    if args.local:
        sess.clear()
    else:
        logout(sess)
    print('Logged out.' + ('' if args.local else ' The server session has ended.'))
    if args.forget:
        print('Password removed from the Keychain.' if keychain_delete(sess.cfg) else 'No stored password found.')


def cmd_cookie(sess, args):
    if args.url:
        sess.cfg['base'] = normalize_base(args.url)
        save_config(sess.cfg)
    header = args.cookie if args.cookie != '-' else sys.stdin.read()
    m = re.search(r"-b '([^']*)'", header) or re.search(r"[Cc]ookie: ([^'\"]*)", header)
    sess.import_cookies(m.group(1) if m else header.strip())
    sess.page()
    print('Cookies imported. The session works.')


def cmd_init(sess, args):
    info = inspect(sess, args.months)
    if args.apply:
        for k, v in info['suggested_defaults'].items():
            if not sess.cfg.get(k) or args.force:
                sess.cfg[k] = v
        save_config(sess.cfg)
        info['current_defaults'] = {'contract': sess.cfg.get('contract'), 'activity': sess.cfg.get('activity')}
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        print_inspect(info)
        if args.apply:
            print('Defaults now: ' + json.dumps(info['current_defaults'], ensure_ascii=False))
        else:
            print('Run `logeto init --apply` to save the suggested defaults, or set them with `logeto config`.')


def cmd_ls(sess, args):
    y, m = parse_month(args.month)
    rows = list_month(sess, y, m)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print_rows(rows)


def cmd_add(sess, args):
    day = parse_day(args.date)
    span, minutes = parse_when(args.when)
    contract = args.contract or sess.cfg.get('contract')
    activity = args.activity or sess.cfg.get('activity')
    rec = add_record(sess, day, span, minutes, args.description, contract, activity, dry_run=True)
    when = f"{rec['from']}-{rec['to']} " if rec['from'] else ''
    print(f"{rec['date']}  {when}({rec['hours']} h)  {rec['activity']}  {rec['contract'] or '(no contract)'}\n"
          f"  {rec['description']}")
    if args.dry_run:
        return
    if not confirm('Save?', args.yes):
        print('Not saved.')
        return
    rec = add_record(sess, day, span, minutes, args.description, contract, activity)
    print(f"Saved as record {rec['key']}.")


def cmd_rm(sess, args):
    y, m = parse_month(args.month)
    rows = {r['key']: r for r in list_month(sess, y, m)}
    for key in args.keys:
        if key not in rows:
            raise LogetoError(f'Record {key} is not in {y}-{m:02d}. Use --month.')
        print_rows([rows[key]])
        if not confirm('Delete?', args.yes):
            print('Not deleted.')
            continue
        delete_record(sess, key, y, m)
        print(f'Deleted record {key}.')


def cmd_catalog(sess, args, what):
    items = catalog(sess, args.refresh)[what]
    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return
    for it in items:
        extra = f"  [{it['time']}]" if what == 'activities' and it.get('time') != 'any' else ''
        print(f"{it['value']:>38}  {it['text']}{extra}" if what == 'activities' else f"{it['value']:>6}  {it['text']}")


def cmd_config(sess, args):
    if args.key:
        if args.key not in DEFAULTS:
            raise LogetoError('Unknown key. Keys: ' + ', '.join(DEFAULTS))
        if args.value is None:
            print(sess.cfg.get(args.key))
            return
        value = args.value
        if args.key == 'contract' and value:
            value = pick(catalog(sess)['contracts'], value, 'contract')['text']
        elif args.key == 'activity' and value:
            value = pick(catalog(sess)['activities'], value, 'activity')['text']
        elif args.key == 'base':
            value = normalize_base(value)
        sess.cfg[args.key] = value or None
        save_config(sess.cfg)
    print(json.dumps(sess.cfg, ensure_ascii=False, indent=2))


def main():
    p = argparse.ArgumentParser(prog='logeto', description='Logeto CLI: an unofficial terminal client for Logeto / '
                                'Výkaz práce timesheets. Not affiliated with Systemart s.r.o.')
    p.add_argument('--version', action='version', version=f'logeto-cli {__version__}')
    sub = p.add_subparsers(dest='cmd', required=True)

    s = sub.add_parser('login', help='log in (on macOS the password goes to the Keychain)')
    s.add_argument('--url', help='your Logeto address, for example https://company.vykazprace.cz')
    s.add_argument('--email')
    s.add_argument('--account', help='company account name, only if the login page asks for it')
    s.add_argument('--password-stdin', action='store_true', help='read the password from stdin')
    s.set_defaults(fn=cmd_login)

    s = sub.add_parser('logout', help='end the session')
    s.add_argument('--local', action='store_true', help='only delete the local cookies, keep the server session')
    s.add_argument('--forget', action='store_true', help='also remove the stored password from the Keychain')
    s.set_defaults(fn=cmd_logout)

    s = sub.add_parser('cookie', help='use a browser session: paste a Cookie header or a "Copy as cURL" command')
    s.add_argument('cookie', help='cookie header, curl command, or - for stdin')
    s.add_argument('--url')
    s.set_defaults(fn=cmd_cookie)

    s = sub.add_parser('init', help="read your company's form setup and your history, suggest defaults")
    s.add_argument('--apply', action='store_true', help='save the suggested defaults (keeps values you set)')
    s.add_argument('--force', action='store_true', help='with --apply: also replace values you set')
    s.add_argument('--months', type=int, default=3, help='months of history to read (default 3)')
    s.add_argument('--json', action='store_true')
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser('ls', help='list the records of a month')
    s.add_argument('month', nargs='?', help='2026-09, 9, this (default) or last')
    s.add_argument('--json', action='store_true')
    s.set_defaults(fn=cmd_ls)

    s = sub.add_parser('add', help='add a record: logeto add today 9:00-17:00 "what I did"')
    s.add_argument('date', help='today, 9.10., 2026-10-09')
    s.add_argument('when', help='9:00-17:30 (span) or 7:30 / 7.5h (total hours)')
    s.add_argument('description')
    s.add_argument('-c', '--contract', help='id or part of the name (default: config)')
    s.add_argument('-a', '--activity', help='name or part of it (default: config)')
    s.add_argument('-n', '--dry-run', action='store_true', help='show the record, do not save')
    s.add_argument('-y', '--yes', action='store_true', help='do not ask before saving')
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser('rm', help='delete records by key (see `logeto ls`)')
    s.add_argument('keys', nargs='+')
    s.add_argument('-m', '--month', help='month of the records (default: this month)')
    s.add_argument('-y', '--yes', action='store_true')
    s.set_defaults(fn=cmd_rm)

    for name in ('contracts', 'activities'):
        s = sub.add_parser(name, help=f'list {name}')
        s.add_argument('--refresh', action='store_true')
        s.add_argument('--json', action='store_true')
        s.set_defaults(fn=lambda sess, args, w=name: cmd_catalog(sess, args, w))

    s = sub.add_parser('config', help='show or set config: ' + ', '.join(DEFAULTS))
    s.add_argument('key', nargs='?')
    s.add_argument('value', nargs='?')
    s.set_defaults(fn=cmd_config)

    args = p.parse_args()
    try:
        args.fn(Session(load_config()), args)
    except LogetoError as e:
        sys.exit(f'logeto: {e}')
    except urllib.error.URLError as e:
        sys.exit(f'logeto: network error: {e}')
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == '__main__':
    main()
