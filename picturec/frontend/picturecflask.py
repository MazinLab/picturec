import flask
from flask_wtf import FlaskForm
from flask_bootstrap import Bootstrap
from flask import request, redirect, url_for, render_template, jsonify
from wtforms import SelectField, SubmitField, StringField
from wtforms.validators import DataRequired
import numpy as np

from picturec.frontend.config import Config
from picturec.pcredis import PCRedis
from picturec.devices import COMMAND_DICT

app = flask.Flask(__name__)
bootstrap = Bootstrap(app)
app.config.from_object(Config)
REDIS_DB = 0

TS_KEYS = ['status:temps:mkidarray:temp', 'status:temps:mkidarray:resistance', 'status:temps:lhetank',
           'status:temps:ln2tank', 'status:feedline1:hemt:gate-voltage-bias',
           'status:feedline2:hemt:gate-voltage-bias', 'status:feedline3:hemt:gate-voltage-bias',
           'status:feedline4:hemt:gate-voltage-bias', 'status:feedline5:hemt:gate-voltage-bias',
           'status:feedline1:hemt:drain-voltage-bias', 'status:feedline2:hemt:drain-voltage-bias',
           'status:feedline3:hemt:drain-voltage-bias', 'status:feedline4:hemt:drain-voltage-bias',
           'status:feedline5:hemt:drain-voltage-bias', 'status:feedline1:hemt:drain-current-bias',
           'status:feedline2:hemt:drain-current-bias', 'status:feedline3:hemt:drain-current-bias',
           'status:feedline4:hemt:drain-current-bias', 'status:feedline5:hemt:drain-current-bias',
           'status:device:sim960:hcfet-control-voltage', 'status:highcurrentboard:current']


redis = PCRedis(host='127.0.0.1', port=6379, db=REDIS_DB, create_ts_keys=TS_KEYS)


# TODO: Add alarms for serial (dis)connections?
# TODO: Only have temperature setpoint and have the program internals convert that to resistance?

@app.route('/', methods=['GET', 'POST'])
@app.route('/main', methods=['GET', 'POST'])
def index():
    # TODO: Add HS Position (and ability to toggle it)
    # TODO: Add commands for start/stop/schedule ramp
    form = FlaskForm()
    therm_headers = ['Thermometer', 'Value']
    therm_data = [['Device', f"{redis.redis_ts.get('status:temps:mkidarray:temp')[1]:.3f} mK"],
                  ['(resistance)', f"{redis.redis_ts.get('status:temps:mkidarray:resistance')[1]:.3f} Ohms"],
                  ['LHe Tank', f"{redis.redis_ts.get('status:temps:lhetank')[1]:.2f} K"],
                  ['LN2 Tank', f"{redis.redis_ts.get('status:temps:ln2tank')[1]:.2f} K"]]

    magnet_vals = [['Magnet current', f"{redis.redis_ts.get('status:highcurrentboard:current')[1]:.3f} A"], # TODO: Add a 'predicted voltage' value (based on SIM960 output * conversion factor)?
                   ['SIM960 control voltage', f"{redis.redis_ts.get('status:device:sim960:hcfet-control-voltage')[1]:.3f} V"],
                   ['Control Mode', redis.read(['device-settings:sim960:mode'])['device-settings:sim960:mode']],
                   ['Heat Switch', redis.read(['status:heatswitch'])['status:heatswitch']]]  # TODO: Allow flipping here?

    return render_template('index.html', form=form, table_headers=therm_headers, table_data=therm_data, current_tableh=magnet_vals)


@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    # TODO
    form = FlaskForm()
    return render_template('dashboard.html', title='Dashboard', form=form)


@app.route('/sim960settings', methods=['GET', 'POST'])
def sim960settings():
    form = Sim960SettingForm()
    if request.method == 'POST':
        # TODO: There must be a different better way to do this (matching redis keys to field labels)
        # TODO: Highlight 'changed' values
        # TODO: Add 'notes' to the side of the string fields about what values are legal
        # TODO: Block changes of specific values
        keys = ['device-settings:sim960:vin-setpoint-mode',
                'device-settings:sim960:vin-setpoint-slew-enable',
                'device-settings:sim960:pid-p:enabled',
                'device-settings:sim960:pid-i:enabled',
                'device-settings:sim960:pid-d:enabled']
        desired_vals = form.data
        current_vals = redis.read(keys)
        for k1, k2, v1, v2 in zip(current_vals.keys(), desired_vals.keys(), current_vals.values(), desired_vals.values()):
            if v1 != v2:
                print(f"Change {k1} from {v1} to {v2}")
                # redis.publish(k1, v2)

        return redirect(url_for('sim960settings'))
    else:
        return render_template('sim960settings.html', title='SIM960 Settings', form=form)


@app.route('/sim921settings', methods=['GET', 'POST'])
def sim921settings():
    form = Sim921SettingForm()
    if request.method == 'POST':
        # TODO: There must be a different better way to do this (matching redis keys to field labels)
        # TODO: Highlight 'changed' values
        # TODO: Add 'notes' to the side of the string fields about what values are legal
        # TODO: Block changes of specific values
        keys = ['device-settings:sim921:resistance-range',
                'device-settings:sim921:excitation-value',
                'device-settings:sim921:excitation-mode',
                'device-settings:sim921:time-constant',
                'device-settings:sim921:output-mode',
                'device-settings:sim921:curve-number']
        desired_vals = form.data
        current_vals = redis.read(keys)
        for k1, k2, v1, v2 in zip(current_vals.keys(), desired_vals.keys(), current_vals.values(), desired_vals.values()):
            if v1 != v2:
                print(f"Change {k1} from {v1} to {v2}")
                # redis.publish(k1, v2)

        return redirect(url_for('sim921settings'))
    else:
        return render_template('sim921settings.html', title='SIM921 Settings', form=form)


@app.route('/hemts', methods=['GET', 'POST'])
def hemts():
    form = FlaskForm()
    return render_template('hemts.html', title='HEMT', form=form)


@app.route('/reporter', methods=['POST'])
def reporter():
    vg_keys = [f'status:feedline{i}:hemt:gate-voltage-bias' for i in [1, 2, 3, 4, 5]]
    id_keys = [f'status:feedline{i}:hemt:drain-current-bias' for i in [1, 2, 3, 4, 5]]
    vd_keys = [f'status:feedline{i}:hemt:drain-voltage-bias' for i in [1, 2, 3, 4, 5]]

    vgs = np.array([redis.redis_ts.get(i) for i in vg_keys])
    ids = np.array([redis.redis_ts.get(i) for i in id_keys])
    vds = np.array([redis.redis_ts.get(i) for i in vd_keys])

    print(vgs[:,1], ids[:, 1], vds[:, 1])
    return jsonify({'vg_times': list(vgs[:, 0]), 'gate_voltages': list(vgs[:, 1]),
                    'id_times': list(ids[:, 0]), 'drain_currents': list(ids[:, 1]),
                    'vd_times': list(vds[:, 0]), 'drain_voltages': list(vds[:, 1])})


@app.route('/tester', methods=['GET', 'POST'])
def tester():
    print('This would be a magnet command!')
    data = {'msg':'nothing'}
    return jsonify(data)


@app.route('/tempvals_n2', methods=['POST'])
def tempvals_n2():
    temperature_key = 'status:temps:ln2tank'
    val = redis.redis_ts.get(temperature_key)
    # print(f"LN2 time/temp: {val}")
    return jsonify({'times': val[0], 'temps': val[1]})


@app.route('/tempvals_he', methods=['POST'])
def tempvals_he():
    temperature_key = 'status:temps:lhetank'
    val = redis.redis_ts.get(temperature_key)
    # print(f"LHe time/temp: {val}")
    return jsonify({'times': val[0], 'temps': val[1]})

@app.route('/device_t', methods=['POST'])
def device_t():
    temperature_key = 'status:temps:mkidarray:temp'
    val = redis.redis_ts.get(temperature_key)
    # print(f"Device time/temp: {val}")
    return jsonify({'times': val[0], 'temps': val[1]})


@app.route('/magnet_current', methods=['POST'])
def magnet_current():
    temperature_key = 'status:highcurrentboard:current'
    val = redis.redis_ts.get(temperature_key)
    # print(f"Magnet time/current: {val}")
    return jsonify({'times': val[0], 'currents': val[1]})


def make_choices(key):
    current_value = redis.read(key, return_dict=False)[0]
    rest = list(COMMAND_DICT[key]['vals'].keys())
    choice = [current_value]
    rest.remove(current_value)
    for i in rest:
        choice.append(i)
    print(choice)
    return choice


class Sim960SettingForm(FlaskForm):
    sim960_setpoint_mode = SelectField('Setpoint Mode', choices=make_choices('device-settings:sim960:vin-setpoint-mode'))
    sim960_enable_setpoint_ramp = SelectField('Internal Setpoint Slew Enable', choices=make_choices('device-settings:sim960:vin-setpoint-slew-enable'))
    sim960_p_on = SelectField('PID: P Enabled', choices=make_choices('device-settings:sim960:pid-p:enabled'))
    sim960_i_on = SelectField('PID: I Enabled', choices=make_choices('device-settings:sim960:pid-i:enabled'))
    sim960_d_on = SelectField('PID: D Enabled', choices=make_choices('device-settings:sim960:pid-d:enabled'))

    sim960_p_value = StringField('PID: P Value', default=redis.read('device-settings:sim960:pid-p:value', return_dict=False)[0])
    sim960_i_value = StringField('PID: I Value', default=redis.read('device-settings:sim960:pid-i:value', return_dict=False)[0])
    sim960_d_value = StringField('PID: D Value', default=redis.read('device-settings:sim960:pid-d:value', return_dict=False)[0])
    sim960_vout_min = StringField('Minimum Output Voltage', default=redis.read('device-settings:sim960:vout-min-limit', return_dict=False)[0])
    sim960_vout_max = StringField('Maximum Output Voltage', default=redis.read('device-settings:sim960:vout-max-limit', return_dict=False)[0])
    sim960_setpoint = StringField('Internal Setpoint (V)', default=redis.read('device-settings:sim960:vin-setpoint', return_dict=False)[0])
    sim960_slew_rate = StringField('Setpoint Slew Rate (V/s)', default=redis.read('device-settings:sim960:vin-setpoint-slew-rate', return_dict=False)[0])

    submit = SubmitField('Update', [DataRequired()])


class Sim921SettingForm(FlaskForm):
    sim921_resistance_range = SelectField('Resistance Range', choices=make_choices('device-settings:sim921:resistance-range'))
    sim921_excitation_val = SelectField('Excitation Value', choices=make_choices('device-settings:sim921:excitation-value'))
    sim921_excitation_mode = SelectField('Excitation Mode', choices=make_choices('device-settings:sim921:excitation-mode'))
    sim921_time_constant = SelectField('Time Constant', choices=make_choices('device-settings:sim921:time-constant'))
    sim921_output_mode = SelectField('Output Mode', choices=make_choices('device-settings:sim921:output-mode'))
    sim921_curve = SelectField('Calibration Curve', choices=make_choices('device-settings:sim921:curve-number'))

    sim921_t_offset = StringField('Temperature Setpoint', default=redis.read('device-settings:sim921:temp-offset', return_dict=False)[0])
    sim921_r_offset = StringField('Resistance Setpoint', default=redis.read('device-settings:sim921:resistance-offset', return_dict=False)[0])
    sim921_t_slope = StringField('Temperature Slope (V/K) { Output = A * (T - Tsetpoint) }', default=redis.read('device-settings:sim921:temp-slope', return_dict=False)[0])
    sim921_r_slope = StringField('Resistance Slope (V/Ohm) { Output = A * (R - Rsetpoint) }', default=redis.read('device-settings:sim921:resistance-slope', return_dict=False)[0])
    sim921_vout = StringField('Output Voltage (V)', default=redis.read('device-settings:sim921:manual-vout', return_dict=False)[0])

    submit = SubmitField('Update', [DataRequired()])

if __name__ == "__main__":

    app.debug=True
    app.run(port=8000)