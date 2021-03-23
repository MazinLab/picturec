import flask
from flask_wtf import FlaskForm
from flask_bootstrap import Bootstrap
from flask import request, redirect, url_for, render_template, jsonify
from wtforms import SelectField, SubmitField, StringField
import numpy as np
import time, datetime
import json
import plotly
from logging import getLogger

import picturec.util as util
from picturec.frontend.config import Config
import picturec.pcredis as redis
from picturec.devices import COMMAND_DICT
import picturec.currentduinoAgent as heatswitch


app = flask.Flask(__name__)
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
           'status:device:sim960:current-setpoint']


DASHDATA = np.load('/picturec/picturec/frontend/dashboard_placeholder.npy')


redis.setup_redis(create_ts_keys=TS_KEYS)


def make_select_fields(key, label):
    field = SelectField(f"{label} - [{redis.read(key)}]", choices = make_select_choices(key))
    submit = SubmitField("Update")
    return field, submit


def make_string_fields(key, label):
    field = SelectField(f"{label} - [{redis.read(key)}]")
    submit = SubmitField("Update")
    return field, submit


def make_select_choices(key):
    current_value = redis.read(key)
    rest = list(COMMAND_DICT[key]['vals'].keys())
    choice = [current_value]
    rest.remove(current_value)
    for i in rest:
        choice.append(i)
    return choice


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


@app.route('/development', methods=['GET', 'POST'])
def development():
    if request.method == 'POST':
        for i in request.form.items():
            print(i)
        return redirect(url_for('development'))
    a = SIM921ExcitationValue()
    b = SIM921ExcitationMode()
    return render_template('development.html', title='Development', a=a, b=b)


class SIM921ExcitationValue(FlaskForm):
    key = 'device-settings:sim921:excitation-value'
    sim921excitationvaluefield, submit = make_select_fields(key, "SIM921 Excitation Value")


class SIM921ExcitationMode(FlaskForm):
    key = 'device-settings:sim921:excitation-mode'
    sim921excitationmodefield, submit = make_select_fields(key, "SIM921 Excitation Value")





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


@app.route('/sim960settings', methods=['GET', 'POST'])
def sim960settings():
    form = Sim960SettingForm()
    if request.method == 'POST':
        # TODO: There must be a different better way to do this (matching redis keys to field labels)
        # TODO: Highlight 'changed' values
        # TODO: Add 'notes' to the side of the string fields about what values are legal, check that they're legal!
        # TODO: Block changes of specific values
        keys = ['device-settings:sim960:vin-setpoint-mode',
                'device-settings:sim960:vin-setpoint-slew-enable',
                'device-settings:sim960:pid-p:enabled',
                'device-settings:sim960:pid-i:enabled',
                'device-settings:sim960:pid-d:enabled',
                'device-settings:sim960:pid-p:value',
                'device-settings:sim960:pid-i:value',
                'device-settings:sim960:pid-d:value',
                'device-settings:sim960:vout-min-limit',
                'device-settings:sim960:vout-max-limit',
                'device-settings:sim960:vin-setpoint',
                'device-settings:sim960:vin-setpoint-slew-rate']

        desired_vals = form.data
        current_vals = redis.read(keys)
        for k1, k2, v1, v2 in zip(current_vals.keys(), desired_vals.keys(), current_vals.values(), desired_vals.values()):
            if v1 != v2:
                print(f"Change {k1} from {v1} to {v2}")
                redis.publish(k1, v2)

    return render_template('sim960settings.html', title='SIM960 Settings', form=form)


@app.route('/sim921settings', methods=['GET', 'POST'])
def sim921settings():
    form = Sim921SettingForm()
    if request.method == 'POST':
        # TODO: There must be a different better way to do this (matching redis keys to field labels)
        # TODO: Highlight 'changed' values
        # TODO: Add 'notes' to the side of the string fields about what values are legal, check that they're legal!
        # TODO: Block changes of specific values
        keys = ['device-settings:sim921:resistance-range',
                'device-settings:sim921:excitation-value',
                'device-settings:sim921:excitation-mode',
                'device-settings:sim921:time-constant',
                'device-settings:sim921:output-mode',
                'device-settings:sim921:curve-number',
                'device-settings:sim921:temp-offset',
                'device-settings:sim921:resistance-offset',
                'device-settings:sim921:temp-slope',
                'device-settings:sim921:resistance-slope',
                'device-settings:sim921:manual-vout']

        desired_vals = form.data
        current_vals = redis.read(keys)
        for k1, k2, v1, v2 in zip(current_vals.keys(), desired_vals.keys(), current_vals.values(), desired_vals.values()):
            if v1 != v2:
                getLogger(__name__).debug(f"Change {k1} from {v1} to {v2}")
                redis.publish(k1, v2)

        return redirect(url_for('sim921settings'))
    else:
        return render_template('sim921settings.html', title='SIM921 Settings', form=form)


@app.route('/hemts', methods=['GET', 'POST'])
def hemts():
    form = FlaskForm()
    return render_template('hemts.html', title='HEMT', form=form)


@app.route('/ramp_settings', methods=['GET', 'POST'])
def ramp_settings():
    form = RampConfigForm()
    if request.method == 'POST':
        # TODO: There must be a different better way to do this (matching redis keys to field labels)
        # TODO: Highlight 'changed' values
        # TODO: Add 'notes' to the side of the string fields about what values are legal, check that they're legal!
        # TODO: Block changes of specific values
        keys = ['device-settings:sim960:ramp-rate',
                'device-settings:sim960:soak-time',
                'device-settings:sim960:soak-current']

        desired_vals = form.data
        current_vals = redis.read(keys)
        for k1, k2, v1, v2 in zip(current_vals.keys(), desired_vals.keys(), current_vals.values(), desired_vals.values()):
            if k1 == 'device-settings:sim960:soak-time':
                v2 = float(v2) * 60
                v1 = float(v1)
            if v1 != v2:
                getLogger(__name__).debug(f"Change {k1} from {v1} to {v2}")
                redis.publish(k1, v2)

        return redirect(url_for('ramp_settings'))
    else:
        return render_template('ramp_settings.html', title='Ramp Settings', form=form)


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

    print(vgs[:,1], ids[:, 1], vds[:, 1])
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


class RampConfigForm(FlaskForm):
    ramp_rate = StringField('Ramp Rate (A/s)', default=redis.read('device-settings:sim960:ramp-rate'))
    soak_time = StringField('Soak Time (m)', default=str(float(redis.read('device-settings:sim960:soak-time'))/60))
    soak_current = StringField('Soak Current (A)', default=redis.read('device-settings:sim960:soak-current'))
    open_hs = SubmitField('Open HS')
    close_hs = SubmitField('Close HS')
    submit = SubmitField('Update')


if __name__ == "__main__":

    util.setup_logging('piccDirector')
    redis.setup_redis(create_ts_keys=TS_KEYS)
    app.debug=True
    app.run(port=8000)
