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
           'status:device:sim960:current-setpoint']

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


DASHDATA = np.load('/picturec/picturec/frontend/dashboard_placeholder.npy')


redis.setup_redis(create_ts_keys=TS_KEYS)

pubsub = red.pubsub()
pubsub.unsubscribe()
pubsub.subscribe('chat')


def make_select_fields(key, label):
    field = SelectField(f"{label}", choices=make_select_choices(key), id=key)
    submit = SubmitField("Update", id=key)
    return field, submit


def make_string_fields(key, label):
    field = StringField(f"{label}", id=key)
    submit = SubmitField("Update")
    return field, submit


def make_select_choices(key):
    choices = list(COMMAND_DICT[key]['vals'].keys())
    return choices


# TODO: Add alarms for serial (dis)connections?


@app.route('/', methods=['GET', 'POST'])
@app.route('/main', methods=['GET', 'POST'])
def index():
    form = MainPageForm()

    init_lhe_d, init_lhe_l = sensor_plot('status:temps:lhetank', 'LHe Temp', 'old')
    init_ln2_d, init_ln2_l = sensor_plot('status:temps:ln2tank', 'LN2 Temp', 'old')
    init_devt_d, init_devt_l = sensor_plot('status:temps:mkidarray:temp', 'Device Temp', 'old')
    init_magc_d, init_magc_l = sensor_plot('status:highcurrentboard:current', 'Measured Current', 'old')
    init_smagc_d, init_smagc_l = sensor_plot('status:device:sim960:current-setpoint', 'Desired Current', 'old')

    return render_template('index.html', form=form, init_lhe_d=init_lhe_d, init_lhe_l=init_lhe_l,
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
    sim921form = (SIM921ResistanceRange(), SIM921ExcitationValue(), SIM921ExcitationMode(), SIM921TimeConstant(),
                  SIM921TempSlope(), SIM921ResSlope(), SIM921CalCurve())
    sim960form = (SIM960VOutMin(), SIM960VoutMax(), SIM960VinSetpointMode(), SIM960VinSetpointValue(),
                  SIM960VinSetpointSlewEnable(), SIM960VinSetpointSlewRate(), SIM960PIDPEnabled(),
                  SIM960PIDIEnabled(), SIM960PIDDEnabled(), SIM960PIDOEnabled(), SIM960PIDPVal(),
                  SIM960PIDIVal(), SIM960PIDDVal(), SIM960PIDOVal())
    hsbutton = HeatswitchToggle()
    return render_template('settings.html', title='Settings', sim921form=sim921form, sim960form=sim960form, hs=hsbutton, rv=rv)


@app.route('/dashboard', methods=['GET'])
def dashboard():
    # TODO
    form = FlaskForm()
    return render_template('dashboard.html', title='Dashboard', form=form)


@app.route('/viewdata', methods=['POST'])
def viewdata():
    frame_to_use = np.random.randint(0, len(DASHDATA))
    x = DASHDATA[frame_to_use][75:200,75:200]
    return jsonify({'cts':x.tolist()})


@app.route('/hemts', methods=['GET', 'POST'])
def hemts():
    form = FlaskForm()
    return render_template('hemts.html', title='HEMT', form=form)


@app.route('/ramp_settings', methods=['GET', 'POST'])
def ramp_settings():
    form = FlaskForm()
    return render_template('ramp_settings.html', title='Ramp Settings', form=form)


@app.route('/stream')
def stream():
    return Response(event_stream(), mimetype="text/event-stream")


def event_stream():
    for message in pubsub.listen():
        print(message)
        if message['type'] == 'message':
            yield 'data: %s\n\n' % message['data'].decode('utf-8')


@app.route('/sensor_plot/<key>/<title>/<typ>', methods=['GET', 'POST'])
def sensor_plot(key, title, typ):
    """
    :param key: Redis key plot data is needed for
    :param title: Plot title. If '-', not used
    :param typ: <'new'|'old'> Type of updating required. 'new' gives the most recent point. 'old' gives up to 30 minutes of data.
    :return: data to be plotted.
    """

    if typ == 'old':
        ts = np.array(redis.pcr_range(key, '-', '+'))
        last_tval = time.time() # In seconds
        first_tval = last_tval - 1800  # Allow data from up to 30 minutes beforehand to be plotted (30 m = 1800 s)
        m = (ts[:,0]/1000 >= first_tval) & (ts[:, 0]/1000 <= last_tval)
        times = [datetime.datetime.fromtimestamp(t/1000).strftime("%H:%M:%S") for t in ts[m][:,0]]
        vals = list(ts[m][:,1])
        if len(times) == 0:
            val = redis.read(key)
            times = [datetime.datetime.fromtimestamp(val[0] / 1000).strftime("%H:%M:%S")]
            vals = [val[1]]
    elif typ == 'new':
        val = redis.read(key)
        times = [datetime.datetime.fromtimestamp(val[0]/1000).strftime("%H:%M:%S")]
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


@app.route('/reporter', methods=['POST'])
def reporter():
    vg_keys = [f'status:feedline{i}:hemt:gate-voltage-bias' for i in [1, 2, 3, 4, 5]]
    id_keys = [f'status:feedline{i}:hemt:drain-current-bias' for i in [1, 2, 3, 4, 5]]
    vd_keys = [f'status:feedline{i}:hemt:drain-voltage-bias' for i in [1, 2, 3, 4, 5]]

    vgs = np.array([redis.read(i) for i in vg_keys])
    ids = np.array([redis.read(i) for i in id_keys])
    vds = np.array([redis.read(i) for i in vd_keys])

    vgtimes = list([datetime.datetime.fromtimestamp(t/1000).strftime("%H:%M:%S") for t in vgs[:, 0]])
    idtimes = list([datetime.datetime.fromtimestamp(t/1000).strftime("%H:%M:%S") for t in ids[:, 0]])
    vdtimes = list([datetime.datetime.fromtimestamp(t/1000).strftime("%H:%M:%S") for t in vds[:, 0]])

    return jsonify({'vg_times': vgtimes, 'gate_voltages': list(vgs[:, 1]),
                    'id_times': idtimes, 'drain_currents': list(ids[:, 1]),
                    'vd_times': vdtimes, 'drain_voltages': list(vds[:, 1])})


@app.route('/start_cooldown', methods=['POST'])
def start_cooldown():
    redis.publish('command:get-cold', 'get-cold')
    data = {'msg': f'Cooldown started at {datetime.datetime.fromtimestamp(time.time()).strftime("%c")}'}
    return jsonify(data)


@app.route('/abort_cooldown', methods=['POST'])
def abort_cooldown():
    redis.publish('command:abort-cooldown', 'abort-cooldown')
    data = {'msg': f'Cooldown aborted at {datetime.datetime.fromtimestamp(time.time()).strftime("%c")}'}
    return jsonify(data)


@app.route('/schedule_be_cold_at', methods=['POST'])
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


@app.route('/opener', methods=['POST'])
def opener():
    heatswitch.open()
    if heatswitch.is_opened():
        data = {'msg': 'Successfully opened heatswitch'}
    else:
        data = {'msg': 'Heatswitch failed to open'}
    return jsonify(data)


@app.route('/closer', methods=['POST'])
def closer():
    heatswitch.close()
    if heatswitch.is_closed():
        data = {'msg': 'Successfully closed heatswitch'}
    else:
        data = {'msg': 'Heatswitch failed to close'}
    return jsonify(data)


class MainPageForm(FlaskForm):
    start_cooldown = SubmitField('Start Cooldown')
    abort_cooldown = SubmitField('Abort Cooldown')
    be_cold_time = StringField("Be cold at", default="HH:MM:SS")
    schedule_cooldown = SubmitField('Schedule')
    cancel_scheduled = SubmitField('Cancel Scheduled Cooldown')


class SIM921ResistanceRange(FlaskForm):
    key = 'device-settings:sim921:resistance-range'
    sim921resistancerange, submit = make_select_fields(key, "Resistance Range (\u03A9)")


class SIM921ExcitationValue(FlaskForm):
    key = 'device-settings:sim921:excitation-value'
    sim921excitationvalue, submit = make_select_fields(key, "Excitation Value (V)")


class SIM921ExcitationMode(FlaskForm):
    key = 'device-settings:sim921:excitation-mode'
    sim921excitationmode, submit = make_select_fields(key, "Excitation Mode")


class SIM921TimeConstant(FlaskForm):
    key = 'device-settings:sim921:time-constant'
    sim921timeconstant, submit = make_select_fields(key, "Time Constant (s)")


class SIM921TempSlope(FlaskForm):
    key = 'device-settings:sim921:temp-slope'
    sim921tempslope, submit = make_string_fields(key, "Temperature Slope (V/K)")


class SIM921ResSlope(FlaskForm):
    key = 'device-settings:sim921:resistance-slope'
    sim921resistanceslope, submit = make_string_fields(key, "Resistance Slope (V/\u03A9)")


class SIM921CalCurve(FlaskForm):
    key = 'device-settings:sim921:curve-number'
    sim921curve, submit = make_select_fields(key, "Calibration Curve")


class SIM960VOutMin(FlaskForm):
    key = 'device-settings:sim960:vout-min-limit'
    sim960voutmin, submit = make_string_fields(key, "Minimum Output (V)")


class SIM960VoutMax(FlaskForm):
    key = 'device-settings:sim960:vout-max-limit'
    sim960voutmax, submit = make_string_fields(key, "Maximum Output (V)")


class SIM960VinSetpointMode(FlaskForm):
    key = 'device-settings:sim960:vin-setpoint-mode'
    sim960vinsetpointmode, submit = make_select_fields(key, "Input Voltage Mode")


class SIM960VinSetpointValue(FlaskForm):
    key = 'device-settings:sim960:vin-setpoint'
    sim960vinsetpointvalue, submit = make_string_fields(key, "Input Voltage Desired Value(V)")


class SIM960VinSetpointSlewEnable(FlaskForm):
    key = 'device-settings:sim960:vin-setpoint-slew-enable'
    sim960vinsetpointslewenable, submit = make_select_fields(key, "Enable Internal Setpoint Slew")


class SIM960VinSetpointSlewRate(FlaskForm):
    key = 'device-settings:sim960:vin-setpoint-slew-rate'
    sim960vinsetpointslewrate, submit = make_string_fields(key, "Internal Setpoint Slew Rate")


class SIM960PIDPVal(FlaskForm):
    key = 'device-settings:sim960:pid-p:value'
    sim960pidpval, submit = make_string_fields(key, "PID: P Value")


class SIM960PIDIVal(FlaskForm):
    key = 'device-settings:sim960:pid-i:value'
    sim960pidival, submit = make_string_fields(key, "PID: I Value")


class SIM960PIDDVal(FlaskForm):
    key = 'device-settings:sim960:pid-d:value'
    sim960piddval, submit = make_string_fields(key, "PID: D Value")


class SIM960PIDOVal(FlaskForm):
    key = 'device-settings:sim960:pid-offset:value'
    sim960pidoval, submit = make_string_fields(key, "PID: Offset Value")


class SIM960PIDPEnabled(FlaskForm):
    key = 'device-settings:sim960:pid-p:enabled'
    sim960pidpenable, submit = make_select_fields(key, "PID: Enable P")


class SIM960PIDIEnabled(FlaskForm):
    key = 'device-settings:sim960:pid-i:enabled'
    sim960pidienable, submit = make_select_fields(key, "PID: Enable I")


class SIM960PIDDEnabled(FlaskForm):
    key = 'device-settings:sim960:pid-d:enabled'
    sim960piddenable, submit = make_select_fields(key, "PID: Enable D")


class SIM960PIDOEnabled(FlaskForm):
    key = 'device-settings:sim960:pid-offset:enabled'
    sim960pidoenable, submit = make_select_fields(key, "PID: Enable Offset")


class HeatswitchToggle(FlaskForm):
    key = 'device-settings:currentduino:heatswitch'
    hsopen = SubmitField('open')
    hsclose = SubmitField('close')


if __name__ == "__main__":
    util.setup_logging('piccDirector')
    redis.setup_redis(create_ts_keys=TS_KEYS)
    app.run(port=8000, threaded=True, debug=True)
