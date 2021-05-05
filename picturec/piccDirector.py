"""
TODO: Make buttons on index page do stuff (actually publish redis commands)
TODO: Simplify adding fields/forms (or at least make it easier, )
TODO: Determine EXACTLY which fields need warning signs
"""

import flask
from flask_wtf import FlaskForm
from flask_bootstrap import Bootstrap
from flask import request, redirect, url_for, render_template, jsonify, Response
from wtforms import SelectField, SubmitField, StringField, RadioField
import numpy as np
import time, datetime
import json
import plotly
from logging import getLogger
from redis import Redis


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
red = Redis(host='localhost', port=6379, db=0)

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

SIM921_SETTING_KEYS = {'resistancerange': 'device-settings:sim921:resistance-range',
              'excitationvalue': 'device-settings:sim921:excitation-value',
              'excitationmode': 'device-settings:sim921:excitation-mode',
              'timeconstant': 'device-settings:sim921:time-constant',
              'tempslope': 'device-settings:sim921:temp-slope',
              'resistanceslope': 'device-settings:sim921:resistance-slope',
              'curve': 'device-settings:sim921:curve-number'}
SIM960_SETTING_KEYS = {'voutmin': 'device-settings:sim960:vout-min-limit',
              'voutmax': 'device-settings:sim960:vout-max-limit',
              'vinsetpointmode': 'device-settings:sim960:vin-setpoint-mode',
              'vinsetpointvalue': 'device-settings:sim960:vin-setpoint',
              'vinsetpointslewenable': 'device-settings:sim960:vin-setpoint-slew-enable',
              'vinsetpointslewrate': 'device-settings:sim960:vin-setpoint-slew-rate',
              'pidpval': 'device-settings:sim960:pid-p:value',
              'pidival': 'device-settings:sim960:pid-i:value',
              'piddval': 'device-settings:sim960:pid-d:value',
              'pidoval': 'device-settings:sim960:pid-offset:value',
              'pidpenable': 'device-settings:sim960:pid-p:enabled',
              'pidienable': 'device-settings:sim960:pid-i:enabled',
              'piddenable': 'device-settings:sim960:pid-d:enabled',
              'pidoenable': 'device-settings:sim960:pid-offset:enabled',
              'hsopen': 'device-settings:currentduino:heatswitch',
              'hsclose': 'device-settings:currentduino:heatswitch'}

SETTING_KEYS = {}
SETTING_KEYS.update(SIM921_SETTING_KEYS)
SETTING_KEYS.update(SIM960_SETTING_KEYS)

MAGNET_COMMAND_FORM_KEYS = {'startcooldown': 'command:get-cold',
                            'abortcooldown': 'command:abort-cooldown',
                            'cancelcooldown': 'command:cancel-scheduled-cooldown',
                            'schedulecooldown': 'command:be-cold-at',
                            'soakcurrent': 'device-settings:sim960:soak-current',
                            'soaktime': 'device-settings:sim960:soak-time',
                            'ramprate': 'device-settings:sim960:ramp-rate',
                            'deramprate': 'device-settings:sim960:deramp-rate',
                            'regulationtemperature': 'device-settings:mkidarray:regulating-temp'}

KEYS = SIM921_KEYS + SIM960_KEYS + LAKESHORE240_KEYS + CURRENTDUINO_KEYS + HEMTTEMP_KEYS + list(COMMAND_DICT.keys())

DASHDATA = np.load('/picturec/picturec/frontend/dashboard_placeholder.npy')


redis.setup_redis(create_ts_keys=TS_KEYS)


@app.route('/listener', methods=["GET"])
def listener():
    return Response(stream(), mimetype='text/event-stream', content_type='text/event-stream')


def stream():
    while True:
        time.sleep(.75)
        x = redis.read(KEYS)
        x = json.dumps(x)
        msg = f"retry:5\ndata: {x}\n\n"
        yield msg


def make_select_choices(key):
    """
    USE: field = SelectField(label, choices=make_select_choices(key), id=key)
    """
    choices = list(COMMAND_DICT[key]['vals'].keys())
    return choices


# TODO: Add alarms for serial (dis)connections?

@app.route('/', methods=['GET', 'POST'])
@app.route('/main', methods=['GET', 'POST'])
def index():
    form = FlaskForm()
    if request.method == 'POST':
        for i in request.form.items():
            if i[1] == "":
                app.logger.warning(f"Invalid format to change value! Empty strings are not accepted")
            else:
                if i[0] in MAGNET_COMMAND_FORM_KEYS.keys():
                    # getLogger(__name__).info(f"command:{FIELD_KEYS[i[0]]} -> {i[1]}")
                    if i[0] == 'schedulecooldown':
                        app.logger.info(f"{i}, {MAGNET_COMMAND_FORM_KEYS[i[0]]}, {i[1]}, {parse_schedule_cooldown(i[1])}")
                        if parse_schedule_cooldown(i[1]):
                            app.logger.info(f"{i}, {MAGNET_COMMAND_FORM_KEYS[i[0]]}, {i[1]}, {parse_schedule_cooldown(i[1])}")
                            # redis.publish(f"{MAGNET_COMMAND_FORM_KEYS[i[0]]}", parse_schedule_cooldown(i[1]))
                    else:
                        app.logger.info(f"{i}, {MAGNET_COMMAND_FORM_KEYS[i[0]]}, {i[1]}")
                        # redis.publish(f"{MAGNET_COMMAND_FORM_KEYS[i[0]]}", i[1])
        # return redirect(url_for('index'))

    init_lhe_d, init_lhe_l = initialize_sensor_plot('status:temps:lhetank', 'LHe Temp')
    init_ln2_d, init_ln2_l = initialize_sensor_plot('status:temps:ln2tank', 'LN2 Temp')
    init_devt_d, init_devt_l = initialize_sensor_plot('status:temps:mkidarray:temp', 'Device Temp')
    init_magc_d, init_magc_l = initialize_sensor_plot('status:highcurrentboard:current', 'Measured Current')
    init_smagc_d, init_smagc_l = initialize_sensor_plot('status:device:sim960:current-setpoint', 'Current')
    init_dash_data, init_dash_layout = viewdata()
    cycleform = CycleControlForm()
    magnetform = MagnetControlForm()
    return render_template('index.html', form=form, init_lhe_d=init_lhe_d, init_lhe_l=init_lhe_l,
                           init_ln2_d=init_ln2_d, init_ln2_l=init_ln2_l, init_devt_d=init_devt_d,
                           init_devt_l=init_devt_l, init_magc_d=init_magc_d, init_magc_l=init_magc_l,
                           init_smagc_d=init_smagc_d, init_smagc_l=init_smagc_l, mag=magnetform, cyc=cycleform,
                           init_data=init_dash_data, init_layout=init_dash_layout)


@app.route('/other_plots', methods=['GET'])
def other_plots():
    form = FlaskForm()
    init_lhe_d, init_lhe_l = initialize_sensor_plot('status:temps:lhetank', 'LHe Temp')
    init_ln2_d, init_ln2_l = initialize_sensor_plot('status:temps:ln2tank', 'LN2 Temp')
    init_devt_d, init_devt_l = initialize_sensor_plot('status:temps:mkidarray:temp', 'Device Temp')
    init_magc_d, init_magc_l = initialize_sensor_plot('status:highcurrentboard:current', 'Measured Current')
    init_smagc_d, init_smagc_l = initialize_sensor_plot('status:device:sim960:current-setpoint', 'Current')
    return render_template('other_plots.html', title='Other Plots', form=form, init_lhe_d=init_lhe_d, init_lhe_l=init_lhe_l,
                           init_ln2_d=init_ln2_d, init_ln2_l=init_ln2_l, init_devt_d=init_devt_d,
                           init_devt_l=init_devt_l, init_magc_d=init_magc_d, init_magc_l=init_magc_l,
                           init_smagc_d=init_smagc_d, init_smagc_l=init_smagc_l)



@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if request.method == 'POST':
        for i in request.form.items():
            if i[0] in SETTING_KEYS.keys():
                getLogger(__name__).info(f"command:{SETTING_KEYS[i[0]]} -> {i[1]}")
                # redis.publish(f"command:{SETTING_KEYS[i[0]]}", i[1], store=False)
        return redirect(url_for('settings'))
    sim921form = SIM921SettingForm()
    sim960form = SIM960SettingForm()
    hsbutton = HeatswitchToggle()
    return render_template('settings.html', title='Settings', s921=sim921form, s960=sim960form, hs=hsbutton)


def viewdata():
    frame_to_use = 50
    x = DASHDATA[frame_to_use][100:175, 100:175]
    z = [{'z': x.tolist(), 'type': 'heatmap'}]
    plot_layout = {'title': 'Device View'}
    d = json.dumps(z, cls=plotly.utils.PlotlyJSONEncoder)
    l = json.dumps(plot_layout, cls=plotly.utils.PlotlyJSONEncoder)
    return d, l


@app.route('/test_page', methods=['GET', 'POST'])
def test_page():
    form = FlaskForm()
    if request.method == 'POST':
        app.logger.debug(request.form)
        key = SETTING_KEYS[request.form.get('id')]
        value = request.form.get('data')
        app.logger.info(f"command:{key} -> {value}")
        try:
            s = SimCommand(key, value)
            is_legal = '\u2713'
        except ValueError:
            is_legal = '\u2717'
        # redis.publish(f"command:{FIELD_KEYS[i[0]]}", i[1], store=False)
        return _validate(key, value)
    sim921form = SIM921SettingForm()
    return render_template('test_page.html', title='Test Page', form=form, s921=sim921form)


@app.route('/validatecmd', methods=['POST'])
def validatecmd():
    key = SETTING_KEYS[request.form.get('id')]
    value = request.form.get('data')
    return _validate(key, value)


def _validate(k, v):
    try:
        s = SimCommand(k, v)
        is_legal = '\u2713'
    except ValueError:
        is_legal = '\u2717'
    return jsonify({'key': k, 'value': v, 'legal': is_legal})


def initialize_sensor_plot(key, title):
    """
    :param key: Redis key plot data is needed for
    :param title: Plot title. If '-', not used
    :param typ: <'new'|'old'> Type of updating required. 'new' gives the most recent point. 'old' gives up to 30 minutes of data.
    :return: data to be plotted.
    """
    last_tval = time.time() # In seconds
    first_tval = int((last_tval - 1800) * 1000)  # Allow data from up to 30 minutes beforehand to be plotted (30 m = 1800 s)
    ts = np.array(redis.pcr_range(key, f"{first_tval}", '+'))
    times = [datetime.datetime.fromtimestamp(t/1000).strftime("%H:%M:%S") for t in ts[:,0]]
    vals = list(ts[:,1])
    if len(times) == 0:
        val = redis.read(key)
        times = [datetime.datetime.fromtimestamp(val[0] / 1000).strftime("%H:%M:%S")]
        vals = [val[1]]

    plot_data = [{
        'x': times,
        'y': vals,
        'name': key
    }]
    plot_layout = {
        'title': title
    }
    d = json.dumps(plot_data, cls=plotly.utils.PlotlyJSONEncoder)
    l = json.dumps(plot_layout, cls=plotly.utils.PlotlyJSONEncoder)
    return d, l


def initialize_hemt_plot(key, title):
    last_tval = time.time()
    first_tval = int((last_tval - 1800) * 1000)
    keys = [f'status:feedline{i}:hemt:{key}' for i in [1, 2, 3, 4, 5]]
    timestreams = [np.array(redis.pcr_range(key, f"{first_tval}", "+")) for key in keys]
    times = [[datetime.datetime.fromtimestamp(t / 1000).strftime("%H:%M:%S") for t in ts[:, 0]] for ts in timestreams]
    vals = [list(ts[:, 1]) for ts in timestreams]

    plot_data = [{'x': j[0],
                  'y': j[1],
                  'name': f"Feedline {i+1} {title}",
                  'mode': 'lines'} for i, j in enumerate(zip(times, vals))]
    plot_layout = {
        'title': title
    }
    d = json.dumps(plot_data, cls=plotly.utils.PlotlyJSONEncoder)
    l = json.dumps(plot_layout, cls=plotly.utils.PlotlyJSONEncoder)
    app.logger.info(d)
    return d, l


class CycleControlForm(FlaskForm):
    startcooldown = SubmitField('Start Cooldown')
    abortcooldown = SubmitField('Abort Cooldown')
    cancelcooldown = SubmitField('Cancel Scheduled Cooldown')
    schedulecooldown = StringField('Schedule Cooldown', id='be-cold-at')
    schedulesubmit = SubmitField("Schedule")


class MagnetControlForm(FlaskForm):
    soakcurrent = StringField('Soak Current (A)', id='device-settings:sim960:soak-current')
    soaktime = StringField("Soak Time (s)", id='device-settings:sim960:soak-time')
    ramprate = StringField("Ramp Rate (A/s)", id='device-settings:sim960:ramp-rate')
    deramprate = StringField("Deramp Rate (A/s)", id='device-settings:sim960:deramp-rate')
    regulationtemperature = StringField("Regulation Temperature (K)", id='device-settings:mkidarray:regulating-temp')
    update = SubmitField("Update")


class SIM921SettingForm(FlaskForm):
    resistancerange = SelectField("\u26A0 Resistance Range (\u03A9)", choices=make_select_choices('device-settings:sim921:resistance-range'))
    excitationvalue = SelectField("\u26A0 Excitation Value (V)", choices=make_select_choices('device-settings:sim921:excitation-value'))
    excitationmode = SelectField("\u26A0 Excitation Mode", choices=make_select_choices('device-settings:sim921:excitation-mode'))
    timeconstant = SelectField("\u26A0 Time Constant (s)", choices=make_select_choices('device-settings:sim921:time-constant'))
    tempslope = StringField("\u26A0 Temperature Slope (V/K)")
    resistanceslope = StringField("\u26A0 Resistance Slope (V/\u03A9)")
    curve = SelectField("\u26A0 Calibration Curve", choices=make_select_choices('device-settings:sim921:curve-number'))
    update = SubmitField("Update")


class SIM960SettingForm(FlaskForm):
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
    update = SubmitField("Update")


class HeatswitchToggle(FlaskForm):
    open = SubmitField('Open', id='open')
    close = SubmitField('Close', id='close')


def parse_schedule_cooldown(schedule_time):
    """
    Takes a string and converts it sensibly to a timestamp to be used by the SIM960 schedule cooldown function
    """
    if schedule_time == '':
        return 0
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
        tval = t[1].split(":")
        hr = int(tval[0])
        minute = int(tval[1])
    else:
        tval = t[0].split(":")
        hr = int(tval[0])
        minute = int(tval[1])

    ts = datetime.datetime(year, month, day, hr, minute).timestamp()
    return ts


if __name__ == "__main__":
    redis.setup_redis(create_ts_keys=TS_KEYS)
    app.run(port=8000, threaded=True, debug=True, ssl_context=('/home/mazinlab/appcerts/cert.pem', '/home/mazinlab/appcerts/key.pem'))
