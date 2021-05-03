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
from picturec.devices import COMMAND_DICT
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

FIELD_KEYS = {'sim921resistancerange': 'device-settings:sim921:resistance-range',
              'sim921excitationvalue': 'device-settings:sim921:excitation-value',
              'sim921excitationmode': 'device-settings:sim921:excitation-mode',
              'sim921timeconstant': 'device-settings:sim921:time-constant',
              'sim921tempslope': 'device-settings:sim921:temp-slope',
              'sim921resistanceslope': 'device-settings:sim921:resistance-slope',
              'sim921curve': 'device-settings:sim921:curve-number',
              'sim960voutmin': 'device-settings:sim960:vout-min-limit',
              'sim960voutmax': 'device-settings:sim960:vout-max-limit',
              'sim960vinsetpointmode': 'device-settings:sim960:vin-setpoint-mode',
              'sim960vinsetpointvalue': 'device-settings:sim960:vin-setpoint',
              'sim960vinsetpointslewenable': 'device-settings:sim960:vin-setpoint-slew-enable',
              'sim960vinsetpointslewrate': 'device-settings:sim960:vin-setpoint-slew-rate',
              'sim960pidpval': 'device-settings:sim960:pid-p:value',
              'sim960pidival': 'device-settings:sim960:pid-i:value',
              'sim960piddval': 'device-settings:sim960:pid-d:value',
              'sim960pidoval': 'device-settings:sim960:pid-offset:value',
              'sim960pidpenable': 'device-settings:sim960:pid-p:enabled',
              'sim960pidienable': 'device-settings:sim960:pid-i:enabled',
              'sim960piddenable': 'device-settings:sim960:pid-d:enabled',
              'sim960pidoenable': 'device-settings:sim960:pid-offset:enabled',
              'hsopen': 'device-settings:currentduino:heatswitch',
              'hsclose': 'device-settings:currentduino:heatswitch'}

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
            if i[0] in MAGNET_COMMAND_FORM_KEYS.keys():
                # getLogger(__name__).info(f"command:{FIELD_KEYS[i[0]]} -> {i[1]}")
                if i[0] == 'schedulecooldown':
                    app.logger.info(f"{i}, {MAGNET_COMMAND_FORM_KEYS[i[0]]}, {i[1]}, {parse_schedule_cooldown(i[1])}")
                    if parse_schedule_cooldown(i[1]):
                        # redis.publish(f"{MAGNET_COMMAND_FORM_KEYS[i[0]]}", parse_schedule_cooldown(i[1]))
                        print('would schedule')
                else:
                    # redis.publish(f"{MAGNET_COMMAND_FORM_KEYS[i[0]]}", i[0])
                    app.logger.info(f"{i}, {MAGNET_COMMAND_FORM_KEYS[i[0]]}, {i[1]}")
        return redirect(url_for('index'))

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
            if i[0] in FIELD_KEYS.keys():
                getLogger(__name__).info(f"command:{FIELD_KEYS[i[0]]} -> {i[1]}")
                redis.publish(f"command:{FIELD_KEYS[i[0]]}", i[1], store=False)
        return redirect(url_for('settings'))
    rv = dict(zip(FIELD_KEYS.keys(), redis.read(FIELD_KEYS.values()).values()))
    sim921form = SIM921SettingForm()
    sim960form = SIM960SettingForm()
    hsbutton = HeatswitchToggle()
    return render_template('settings.html', title='Settings', s921=sim921form, s960=sim960form, hs=hsbutton, rv=rv)


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
        for i in request.form.items():
            if i[0] in MAGNET_COMMAND_FORM_KEYS.keys():
                # redis.publish(f"{MAGNET_COMMAND_FORM_KEYS[i[0]]}", i[0])
                app.logger.info(f"{i}, {MAGNET_COMMAND_FORM_KEYS[i[0]]}")
        return redirect(url_for('test_page'))
    x = MagnetControlForm()
    return render_template('test_page.html', title='Test Page', form=form, a=x)




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
    resistancerange = SelectField("Resistance Range (\u03A9)", choices=make_select_choices('device-settings:sim921:resistance-range'))
    excitationvalue = SelectField("Excitation Value (V)", choices=make_select_choices('device-settings:sim921:excitation-value'))
    excitationmode = SelectField("Excitation Mode", choices=make_select_choices('device-settings:sim921:excitation-mode'))
    timeconstant = SelectField("Time Constant (s)", choices=make_select_choices('device-settings:sim921:time-constant'))
    tempslope = StringField("\u26A0 Temperature Slope (V/K)", id='device-settings:sim921:temp-slope')
    resistanceslope = StringField("\u26A0 Resistance Slope (V/\u03A9)", id='device-settings:sim921:resistance-slope')
    curve = SelectField("Calibration Curve", choices=make_select_choices('device-settings:sim921:curve-number'))
    update = SubmitField("Update")


class SIM960SettingForm(FlaskForm):
    voutmin = StringField("Minimum Output (V)", id='device-settings:sim960:vout-min-limit')
    voutmax = StringField("Maximum Output (V)", id='device-settings:sim960:vout-max-limit')
    vinsetpointmode = SelectField("Input Voltage Mode", choices=make_select_choices('device-settings:sim960:vin-setpoint-mode'))
    vinsetpointvalue = StringField("\u26A0 Input Voltage Desired Value(V)", id='device-settings:sim960:vin-setpoint')
    vinsetpointslewenable = SelectField("Enable Internal Setpoint Slew", choices=make_select_choices('device-settings:sim960:vin-setpoint-slew-enable'))
    vinsetpointslewrate = StringField("Internal Setpoint Slew Rate", id='device-settings:sim960:vin-setpoint-slew-rate')
    pidpval = StringField("PID: P Value", id='device-settings:sim960:pid-p:value')
    pidival = StringField("PID: I Value", id='device-settings:sim960:pid-i:value')
    piddval = StringField("PID: D Value", id='device-settings:sim960:pid-d:value')
    pidoval = StringField("PID: Offset Value", id='device-settings:sim960:pid-offset:value')
    pidpenable = SelectField("PID: Enable P", choices=make_select_choices('device-settings:sim960:pid-p:enabled'))
    pidienable = SelectField("PID: Enable I", choices=make_select_choices('device-settings:sim960:pid-i:enabled'))
    piddenable = SelectField("PID: Enable D", choices=make_select_choices('device-settings:sim960:pid-d:enabled'))
    pidoenable = SelectField("PID: Enable Offset", choices=make_select_choices('device-settings:sim960:pid-offset:enabled'))
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



def start_cooldown():
    redis.publish('command:get-cold', 'get-cold')
    data = {'msg': f'Cooldown started at {datetime.datetime.fromtimestamp(time.time()).strftime("%c")}'}
    return jsonify(data)


def abort_cooldown():
    redis.publish('command:abort-cooldown', 'abort-cooldown')
    data = {'msg': f'Cooldown aborted at {datetime.datetime.fromtimestamp(time.time()).strftime("%c")}'}
    return jsonify(data)


def schedule_be_cold_at():
    try:
        stime = [int(i) for i in request.form['time'].split(":")]
    except ValueError:
        data = {'msg': f'Illegal format! Cannot use {request.form["time"]}'}
        return jsonify(data)
    today = datetime.date.today()
    if len(stime) == 2:
        stime.append(0)
    t = datetime.time(stime[0], stime[1], stime[2])
    time_to_be_cold = datetime.datetime.timestamp(datetime.datetime.combine(today, t))
    redis.publish('command:be-cold-at', time_to_be_cold)
    data = {'msg':f'Scheduled to be cold at {datetime.datetime.fromtimestamp(time_to_be_cold).strftime("%c")}'}
    return jsonify(data)


@app.route('/cancel_scheduled_cooldown', methods=['POST'])
def cancel_scheduled_cooldown():
    redis.publish('command:cancel-scheduled-cooldown', 'cancel-scheduled-cooldown')
    data = {'msg': f'Scheduled cooldown has been cancelled!'}
    return jsonify(data)


if __name__ == "__main__":
    redis.setup_redis(create_ts_keys=TS_KEYS)
    app.run(port=8000, threaded=True, debug=True, ssl_context=('/home/mazinlab/appcerts/cert.pem', '/home/mazinlab/appcerts/key.pem'))
