"""
TODO: Make buttons on index page do stuff (actually publish redis commands)
TODO: Determine EXACTLY which fields need warning signs
TODO: Fix errors if redis range is empty
"""

import flask
from flask_wtf import FlaskForm
from flask_bootstrap import Bootstrap
from flask import request, redirect, url_for, render_template, jsonify, Response
from wtforms import SelectField, SubmitField, StringField
import numpy as np
import time, datetime
import json
import plotly
from logging import getLogger
import sys
import subprocess
import select


import picturec.util as util
from picturec.frontend.config import Config
import picturec.pcredis as redis
from picturec.devices import COMMAND_DICT, SimCommand
import picturec.currentduinoAgent as heatswitch

from picturec.sim960Agent import SIM960_KEYS
from picturec.sim921Agent import SIM921_KEYS
from picturec.lakeshore240Agent import LAKESHORE240_KEYS
from picturec.hemttempAgent import HEMTTEMP_KEYS
from picturec.currentduinoAgent import CURRENTDUINO_KEYS
# util.setup_logging('piccDirector')

app = flask.Flask(__name__)
app.logger.setLevel('DEBUG')
bootstrap = Bootstrap(app)
app.config.from_object(Config)

TS_KEYS = ['status:temps:mkidarray:temp', 'status:temps:mkidarray:resistance', 'status:temps:lhetank',
           'status:temps:ln2tank', 'status:feedline1:hemt:gate-voltage-bias',
           'status:feedline2:hemt:gate-voltage-bias', 'status:feedline3:hemt:gate-voltage-bias',
           'status:feedline4:hemt:gate-voltage-bias', 'status:feedline5:hemt:gate-voltage-bias',
           'status:feedline1:hemt:drain-voltage-bias', 'status:feedline2:hemt:drain-voltage-bias',
           'status:feedline3:hemt:drain-voltage-bias', 'status:feedline4:hemt:drain-voltage-bias',
           'status:feedline5:hemt:drain-voltage-bias', 'status:feedline1:hemt:drain-current-bias',
           'status:feedline2:hemt:drain-current-bias', 'status:feedline3:hemt:drain-current-bias',
           'status:feedline4:hemt:drain-current-bias', 'status:feedline5:hemt:drain-current-bias',
           'status:device:sim960:hcfet-control-voltage', 'status:highcurrentboard:current',
           'status:device:sim960:current-setpoint', 'status:device:sim921:sim960-vout', 'status:device:sim960:vin']

SIM921_SETTING_KEYS = {'resistancerange': {'key':'device-settings:sim921:resistance-range', 'type': 'sim921', 'prefix': True},
              'excitationvalue': {'key':'device-settings:sim921:excitation-value', 'type': 'sim921', 'prefix': True},
              'excitationmode': {'key':'device-settings:sim921:excitation-mode', 'type': 'sim921', 'prefix': True},
              'timeconstant': {'key':'device-settings:sim921:time-constant', 'type': 'sim921', 'prefix': True},
              'tempslope': {'key':'device-settings:sim921:temp-slope', 'type': 'sim921', 'prefix': True},
              'resistanceslope': {'key':'device-settings:sim921:resistance-slope', 'type': 'sim921', 'prefix': True},
              'curve': {'key':'device-settings:sim921:curve-number', 'type': 'sim921', 'prefix': True}}
SIM960_SETTING_KEYS = {'voutmin': {'key':'device-settings:sim960:vout-min-limit', 'type':'sim960', 'prefix': True},
              'voutmax': {'key':'device-settings:sim960:vout-max-limit', 'type':'sim960', 'prefix': True},
              'vinsetpointmode': {'key':'device-settings:sim960:vin-setpoint-mode', 'type':'sim960', 'prefix': True},
              'vinsetpointvalue': {'key':'device-settings:sim960:vin-setpoint', 'type':'sim960', 'prefix': True},
              'vinsetpointslewenable': {'key':'device-settings:sim960:vin-setpoint-slew-enable', 'type':'sim960', 'prefix': True},
              'vinsetpointslewrate': {'key':'device-settings:sim960:vin-setpoint-slew-rate', 'type':'sim960', 'prefix': True},
              'pidpval': {'key':'device-settings:sim960:pid-p:value', 'type':'sim960', 'prefix': True},
              'pidival': {'key':'device-settings:sim960:pid-i:value', 'type':'sim960', 'prefix': True},
              'piddval': {'key':'device-settings:sim960:pid-d:value', 'type':'sim960', 'prefix': True},
              'pidoval': {'key':'device-settings:sim960:pid-offset:value', 'type':'sim960', 'prefix': True},
              'pidpenable': {'key':'device-settings:sim960:pid-p:enabled', 'type':'sim960', 'prefix': True},
              'pidienable': {'key':'device-settings:sim960:pid-i:enabled', 'type':'sim960', 'prefix': True},
              'piddenable': {'key':'device-settings:sim960:pid-d:enabled', 'type':'sim960', 'prefix': True},
              'pidoenable': {'key':'device-settings:sim960:pid-offset:enabled', 'type':'sim960', 'prefix': True}}
HEATSWITCH_SETTING_KEYS = {'open': {'key':'device-settings:currentduino:heatswitch', 'type':'heatswitch', 'prefix': True},
                           'close': {'key':'device-settings:currentduino:heatswitch', 'type':'heatswitch', 'prefix': True}}

MAGNET_COMMAND_FORM_KEYS = {'soakcurrent': {'key':'device-settings:sim960:soak-current', 'type':'magnet', 'prefix': False},
                            'soaktime': {'key':'device-settings:sim960:soak-time', 'type':'magnet', 'prefix': False},
                            'ramprate': {'key':'device-settings:sim960:ramp-rate', 'type':'magnet', 'prefix': False},
                            'deramprate': {'key':'device-settings:sim960:deramp-rate', 'type':'magnet', 'prefix': False},
                            'regulationtemperature': {'key':'device-settings:mkidarray:regulating-temp', 'type':'magnet', 'prefix': True}}

CYCLE_KEYS = {'startcooldown': {'key':'command:get-cold', 'type': 'cycle', 'prefix': False, 'schedule':False},
              'abortcooldown': {'key':'command:abort-cooldown', 'type': 'cycle', 'prefix': False, 'schedule':False},
              'cancelcooldown': {'key':'command:cancel-scheduled-cooldown', 'type': 'cycle', 'prefix': False, 'schedule':False},
              'schedulecooldown': {'key':'command:be-cold-at', 'type': 'cycle', 'prefix': False, 'schedule':True}}

CHART_KEYS = {'Device T':'status:temps:mkidarray:temp',
              'LHe T':'status:temps:lhetank',
              'LN2 T':'status:temps:ln2tank',
              'Magnet I':'status:device:sim960:current-setpoint',
              'Measured I':'status:highcurrentboard:current'}

FIELD_KEYS = {}
FIELD_KEYS.update(SIM921_SETTING_KEYS)
FIELD_KEYS.update(SIM960_SETTING_KEYS)
FIELD_KEYS.update(HEATSWITCH_SETTING_KEYS)
FIELD_KEYS.update(MAGNET_COMMAND_FORM_KEYS)
FIELD_KEYS.update(CYCLE_KEYS)

KEYS = SIM921_KEYS + SIM960_KEYS + LAKESHORE240_KEYS + CURRENTDUINO_KEYS + HEMTTEMP_KEYS + list(COMMAND_DICT.keys())

DASHDATA = np.load('/picturec/picturec/frontend/dashboard_placeholder.npy')


redis.setup_redis(create_ts_keys=TS_KEYS)

# ----------------------------------- Flask Endpoints for Pages Below -----------------------------------
@app.route('/', methods=['GET', 'POST'])
@app.route('/main', methods=['GET', 'POST'])
def index():
    """
    Flask endpoint for the main app page.
    Processes requests from the magnet cycle form (start/abort/cancel/schedule cooldown) and magnet form (ramp rates/
    soak settings) and publishes them to be interpreted by the necessary agents.
    Initializes sensor plot data to send for plotting.
    TODO: Device Viewer - Currently has placeholder buttons/information/'view'
    """
    form = FlaskForm()
    if request.method == 'POST':
        return handle_post(request)

    d,l,c = initialize_sensors_plot(CHART_KEYS.keys())
    dd, dl, dc = viewdata()
    cycleform = CycleControlForm()
    magnetform = MagnetControlForm()
    return render_template('index.html', form=form, mag=magnetform, cyc=cycleform,
                           d=d, l=l, c=c, dd=dd, dl=dl, dc=dc)


@app.route('/other_plots', methods=['GET'])
def other_plots():
    """
    Flask endpoint for 'other plots'. This page has ALL sensor plots in one place for convenience (in contrast to index,
    which only has one at a time).
    """
    form = FlaskForm()
    lhe_d, lhe_l, lhe_c = initialize_sensor_plot('LHe T')
    ln2_d, ln2_l, ln2_c = initialize_sensor_plot('LN2 T')
    devt_d, devt_l, devt_c = initialize_sensor_plot('Device T')
    magc_d, magc_l, magc_c = initialize_sensor_plot('Measured I')
    smagc_d, smagc_l, smagc_c = initialize_sensor_plot('Magnet I')
    return render_template('other_plots.html', title='Other Plots', form=form,
                           lhe_d=lhe_d, lhe_l=lhe_l, lhe_c=lhe_c, ln2_d=ln2_d, ln2_l=ln2_l, ln2_c=ln2_c,
                           devt_d=devt_d, devt_l=devt_l, devt_c=devt_c, magc_d=magc_d, magc_l=magc_l, magc_c=magc_c,
                           smagc_d=smagc_d, smagc_l=smagc_l, smagc_c=smagc_c)


@app.route('/settings', methods=['GET', 'POST'])
def settings():
    """
    Flask endpoint for settings page. Handles setting changes for housekeeping instruments
    TODO: Readout settings (when we have a readout)
    """
    if request.method == 'POST':
        return handle_post(request)

    sim921form = SIM921SettingForm()
    sim960form = SIM960SettingForm()
    forms = [sim921form, sim960form]
    hsbutton = HeatswitchToggle()
    return render_template('settings.html', title='Settings', hs=hsbutton, forms=forms)


@app.route('/log_viewer', methods=['GET', 'POST'])
def log_viewer():
    """
    Flask endpoint for log viewer. This page is solely for observing the journalctl output from each agent.
    """
    form = FlaskForm()
    return render_template('log_viewer.html', title='Log Viewer', form=form)


@app.route('/test_page', methods=['GET', 'POST'])
def test_page():
    """
    Test area for trying out things before implementing them on a page
    """
    tform = TestForm()
    return render_template('test_page.html', title='Test Page', form=tform)


# ----------------------------------- Helper Functions Below -----------------------------------
def viewdata():
    """
    Placeholding function to grab a frame from a (hard-coded, previously made) temporal drizzle to display as the
    'device view' on the homepage of the flask application.
    """
    frame_to_use = 50
    x = DASHDATA[frame_to_use][100:175, 100:175]
    z = [{'z': x.tolist(), 'type': 'heatmap', 'showscale':False}]
    plot_layout = {'title': 'Device View'}
    plot_config = {'responsive': True}
    d = json.dumps(z, cls=plotly.utils.PlotlyJSONEncoder)
    l = json.dumps(plot_layout, cls=plotly.utils.PlotlyJSONEncoder)
    c = json.dumps(plot_config, cls=plotly.utils.PlotlyJSONEncoder)
    return d, l, c


def make_select_choices(key):
    """
    Creates a list to use in a flask SelectField. Takes the allowable values from the devices.py COMMAND_DICT
    """
    choices = list(COMMAND_DICT[key]['vals'].keys())
    return choices


@app.route('/dashlistener', methods=["GET"])
def dashlistener():
    """
    listener is a function that implements the python (server) side of a server sent event (SSE) communication protocol
    where data can be streamed directly to the flask app.
    """
    def stream():
        while True:
            time.sleep(.5)
            x = DASHDATA[np.random.randint(0, len(DASHDATA))][100:175, 100:175]
            z = [{'z': x.tolist(), 'type': 'heatmap', 'showscale': False}]
            x = json.dumps(z, cls=plotly.utils.PlotlyJSONEncoder)
            msg = f"retry:5\ndata: {x}\n\n"
            yield msg
    return Response(stream(), mimetype='text/event-stream', content_type='text/event-stream')



@app.route('/listener', methods=["GET"])
def listener():
    """
    listener is a function that implements the python (server) side of a server sent event (SSE) communication protocol
    where data can be streamed directly to the flask app.
    """
    def stream():
        while True:
            time.sleep(.75)
            x = redis.read(KEYS)
            x = json.dumps(x)
            msg = f"retry:5\ndata: {x}\n\n"
            yield msg
    return Response(stream(), mimetype='text/event-stream', content_type='text/event-stream')


@app.route('/journalctl_streamer/<service>')
def journalctl_streamer(service):
    """
    journalctl streamer is another SSE server-side function. The name of an agent (or systemd service, they are the
    same) is passed as an argument and the log messages from that service will then be streamed to wherever this
    endpoint is called.
    """
    args = ['journalctl', '--lines', '0', '--follow', f'_SYSTEMD_UNIT={service}.service']
    def st(arg):
        f = subprocess.Popen(arg, stdout=subprocess.PIPE)
        p = select.poll()
        p.register(f.stdout)
        while True:
            if p.poll(100):
                line = f.stdout.readline()
                yield f"retry:5\ndata: {line.strip().decode('utf-8')}\n\n"
    return Response(st(args), mimetype='text/event-stream', content_type='text/event-stream')


def handle_post(req):
    id = req.form.get('id')
    value = req.form.get('data')
    field_info = FIELD_KEYS[id]
    key = field_info['key']
    field_type = field_info['type']
    prefix_cmd = field_info['prefix']

    app.logger.info(f"For field {id} (key: {key}), changing value to {value} with {field_type} methods.")
    if field_type in ('sim921', 'sim960', 'heatswitch', 'magnet'):
        try:
            s = SimCommand(key, value)
            if prefix_cmd:
                app.logger.debug(f"Sending command:{key} -> {value}")
                # redis.publish(f"command:{key}", value, store=False)
            else:
                app.logger.debug(f"Sending {key} -> {value}")
                # redis.publish(key, value)
        except ValueError:
            pass
        return _validate_cmd(key, value)
    elif field_type == 'cycle':
        if field_info['schedule']:
            x = parse_schedule_cooldown(value)
            app.logger.debug(f"{key} -> {value}, {x[0]}")
            # redis.publish(key, x[0], store=False)
            return _validate_cmd(key, value, schedule=True)
        else:
            app.logger.debug(f"{key} at {time.time()}")
            # redis.publish(key, f"{time.time()}", store=False)
            return jsonify({'mag': True, 'key': key, 'value': time.strftime("%m/%d/%y %H:%M:%S"), 'legal': [True, '\u2713']})
    else:
        app.logger.critical(f"Field type '{field_type}' not implemented!")



def _validate_cmd(k, v, schedule=False):
    """
    Takes a key (k) and value (v) and determines if it is a legal command. Used by /settings and /main to process
    submitted data as well as validate_cmd_change for 'real time' command processing.

    If schedule==True -> validate if the 'schedule cooldown' submittable string field is in an allowable format.
    It looks for both the correct format and if the time is nominally long enough to schedule (~90 minutes in the future)
    """
    if not schedule:
        try:
            s = SimCommand(k, v)
            is_legal = [True, '\u2713']
        except ValueError:
            is_legal = [False, '\u2717']
        print(k, v, is_legal)
        return jsonify({'mag':False, 'key': k, 'value': v, 'legal': is_legal})
    else:
        try:
            x = parse_schedule_cooldown(v)
            if x[2] >= (90 * 60):
                return jsonify({'mag': True, 'key': 'command:be-cold-at', 'value': datetime.datetime.strftime(x[1], "%m/%d/%y %H:%M:%S"), 'legal': [True, '\u2713']})
            else:
                return jsonify({'mag': True, 'key': 'command:be-cold-at', 'value': datetime.datetime.strftime(x[1], "%m/%d/%y %H:%M:%S"), 'legal': [False, '\u2717']})
        except Exception as e:
            return jsonify({'mag': True, 'key': 'command:be-cold-at', 'value': v, 'legal': [False, '\u2717']})


@app.route('/validatecmd', methods=['POST'])
def validate_cmd_change():
    """
    Flask endpoint which is called from an AJAX request when new data is typed/entered into a submittable field. This
    will then report back if the value is allowed or not and report that to the user accordingly (with a check or X)
    """
    field_info = FIELD_KEYS[request.form.get('id')]
    key = field_info['key']
    value = request.form.get('data')
    if 'schedule' in field_info.keys() and field_info['schedule']:
        return _validate_cmd(key, value, schedule=True)
    else:
        return _validate_cmd(key, value)


def initialize_sensor_plot(title):
    """
    :param key: Redis key plot data is needed for
    :param title: Plot title. If '-', not used
    :param typ: <'new'|'old'> Type of updating required. 'new' gives the most recent point. 'old' gives up to 30 minutes of data.
    :return: data to be plotted.
    """
    last_tval = time.time() # In seconds
    first_tval = int((last_tval - 1800) * 1000)  # Allow data from up to 30 minutes beforehand to be plotted (30 m = 1800 s)
    ts = np.array(redis.pcr_range(CHART_KEYS[title], f"{first_tval}", '+'))
    times = [datetime.datetime.fromtimestamp(t/1000).strftime("%H:%M:%S") for t in ts[:,0]]
    vals = list(ts[:,1])
    if len(times) == 0:
        val = redis.read(CHART_KEYS[title])
        times = [datetime.datetime.fromtimestamp(val[0] / 1000).strftime("%H:%M:%S")]
        vals = [val[1]]

    plot_data = [{'x': times,'y': vals,'name': title}]
    plot_layout = {'title': title}
    plot_config = {'responsive': True}
    d = json.dumps(plot_data, cls=plotly.utils.PlotlyJSONEncoder)
    l = json.dumps(plot_layout, cls=plotly.utils.PlotlyJSONEncoder)
    c = json.dumps(plot_config, cls=plotly.utils.PlotlyJSONEncoder)
    return d, l, c


def initialize_sensors_plot(titles):
    last_tval = time.time()
    first_tval = int((last_tval - 1800) * 1000)
    keys = [CHART_KEYS[i] for i in titles]
    timestreams = [np.array(redis.pcr_range(key, f"{first_tval}", "+")) for key in keys]
    times = [[datetime.datetime.fromtimestamp(t / 1000).strftime("%H:%M:%S") for t in ts[:, 0]] for ts in timestreams]
    vals = [list(ts[:, 1]) for ts in timestreams]

    update_menus = []
    for n, t in enumerate(titles):
        visible = [False] * len(titles)
        visible[n] = True
        t_dict = dict(label=str(t),
                      method='update',
                      args=[{'visible': visible}])#, {'title': t}])
        update_menus.append(t_dict)

    plot_data = [{'x': i, 'y': j, 'name': t, 'mode': 'lines', 'visible': False} for i, j, t in
                 zip(times, vals, titles)]
    plot_data[0]['visible'] = True
    plot_layout = dict(updatemenus=list([dict(buttons=update_menus, x=0.01, xanchor='left', y=1.1, yanchor='top')]))
    plot_config = {'responsive': True}
    d = json.dumps(plot_data, cls=plotly.utils.PlotlyJSONEncoder)
    l = json.dumps(plot_layout, cls=plotly.utils.PlotlyJSONEncoder)
    c = json.dumps(plot_config, cls=plotly.utils.PlotlyJSONEncoder)
    return d, l, c


def parse_schedule_cooldown(schedule_time):
    """
    Takes a string input from the schedule cooldown field and parses it to determine if it is in a proper format to be
    used as a time for scheduling a cooldown.
    Returns a timestamp in seconds (to send to the SIM960 agent for scheduling), a datetime object (for reporting to
    flask page), and time until the desired cold time in seconds (to check for it being allowable)
    """
    t = schedule_time.split(" ")
    now = datetime.datetime.now()
    year = now.year
    month = now.month
    day = now.day
    if len(t) == 2:
        sked_type = 'date'
    else:
        sked_type = 'time'

    if sked_type == 'date':
        d = t[0].split('/')
        month = int(d[0])
        day = int(d[1])
        if (len(d[2]) == 2) and (d[2][0:2] != 20):
            year = int('20'+d[2])
        else:
            year = int(d[2])
        tval = t[1].split(":")
        hr = int(tval[0])
        minute = int(tval[1])
        print(year, month, day)
    else:
        tval = t[0].split(":")
        hr = int(tval[0])
        minute = int(tval[1])

    be_cold_at = datetime.datetime(year, month, day, hr, minute)
    tdelta = (be_cold_at - datetime.datetime.now()).total_seconds()
    ts = be_cold_at.timestamp()
    return ts, be_cold_at, tdelta

# ----------------------------------- Custom Flask Forms Below -----------------------------------
class TestForm(FlaskForm):
    pass

class CycleControlForm(FlaskForm):
    startcooldown = SubmitField('Start Cooldown')
    abortcooldown = SubmitField('Abort Cooldown')
    cancelcooldown = SubmitField('Cancel Scheduled Cooldown')
    schedulecooldown = StringField('Schedule Cooldown')


class MagnetControlForm(FlaskForm):
    soakcurrent = StringField('Soak Current (A)')
    soaktime = StringField("Soak Time (s)")
    ramprate = StringField("Ramp Rate (A/s)")
    deramprate = StringField("Deramp Rate (A/s)")
    regulationtemperature = SelectField("Regulation Temperature (K)", choices=make_select_choices("device-settings:mkidarray:regulating-temp"))


class SIM921SettingForm(FlaskForm):
    title = "SIM 921"
    resistancerange = SelectField("\u26A0 Resistance Range (\u03A9)", choices=make_select_choices('device-settings:sim921:resistance-range'))
    excitationvalue = SelectField("\u26A0 Excitation Value (V)", choices=make_select_choices('device-settings:sim921:excitation-value'))
    excitationmode = SelectField("\u26A0 Excitation Mode", choices=make_select_choices('device-settings:sim921:excitation-mode'))
    timeconstant = SelectField("\u26A0 Time Constant (s)", choices=make_select_choices('device-settings:sim921:time-constant'))
    tempslope = StringField("\u26A0 Temperature Slope (V/K)")
    resistanceslope = StringField("\u26A0 Resistance Slope (V/\u03A9)")
    curve = SelectField("\u26A0 Calibration Curve", choices=make_select_choices('device-settings:sim921:curve-number'))


class SIM960SettingForm(FlaskForm):
    title = "SIM 960"
    voutmin = StringField("\u26A0 Minimum Output (V)")
    voutmax = StringField("\u26A0 Maximum Output (V)")
    vinsetpointmode = SelectField("\u26A0 Input Voltage Mode", choices=make_select_choices('device-settings:sim960:vin-setpoint-mode'))
    vinsetpointvalue = StringField("\u26A0 Input Voltage Desired Value(V)")
    vinsetpointslewenable = SelectField("\u26A0 Enable Internal Setpoint Slew", choices=make_select_choices('device-settings:sim960:vin-setpoint-slew-enable'))
    vinsetpointslewrate = StringField("\u26A0 Internal Setpoint Slew Rate")
    pidpval = StringField("\u26A0 PID: P Value")
    pidival = StringField("\u26A0 PID: I Value")
    piddval = StringField("\u26A0 PID: D Value")
    pidoval = StringField("\u26A0 PID: Offset Value")
    pidpenable = SelectField("\u26A0 PID: Enable P", choices=make_select_choices('device-settings:sim960:pid-p:enabled'))
    pidienable = SelectField("\u26A0 PID: Enable I", choices=make_select_choices('device-settings:sim960:pid-i:enabled'))
    piddenable = SelectField("\u26A0 PID: Enable D", choices=make_select_choices('device-settings:sim960:pid-d:enabled'))
    pidoenable = SelectField("\u26A0 PID: Enable Offset", choices=make_select_choices('device-settings:sim960:pid-offset:enabled'))


class HeatswitchToggle(FlaskForm):
    open = SubmitField('Open', id='open')
    close = SubmitField('Close', id='close')


if __name__ == "__main__":
    redis.setup_redis(create_ts_keys=TS_KEYS)
    app.run(port=8000, threaded=True, debug=True, ssl_context=('/home/mazinlab/appcerts/cert.pem', '/home/mazinlab/appcerts/key.pem'))
