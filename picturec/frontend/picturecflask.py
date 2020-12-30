import flask
from flask_wtf import FlaskForm
from flask import request, redirect, url_for, render_template, jsonify
from wtforms import SelectField, SubmitField
from wtforms.validators import DataRequired

from picturec.frontend.config import Config
from picturec.pcredis import PCRedis
from picturec.devices import COMMAND_DICT

app = flask.Flask(__name__)
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


@app.route('/', methods=['GET', 'POST'])
@app.route('/main', methods=['GET', 'POST'])
def index():
    form = FlaskForm()
    therm_headers = ['Thermometer', 'Value']
    therm_data = [['Device', f"{redis.redis_ts.get('status:temps:mkidarray:temp')[1]:.3f} mK"],
                  ['(resistance)', f"{redis.redis_ts.get('status:temps:mkidarray:resistance')[1]:.3f} Ohms"],
                  ['LHe Tank', f"{redis.redis_ts.get('status:temps:lhetank')[1]:.2f} K"],
                  ['LN2 Tank', f"{redis.redis_ts.get('status:temps:ln2tank')[1]:.2f} K"]]

    hemt_headers = ['Hemt', 'Vg', 'Id', 'Vd']
    hemt_vals = [["1", f"{redis.redis_ts.get('status:feedline1:hemt:gate-voltage-bias')[1]:.3f} V", f"{redis.redis_ts.get('status:feedline1:hemt:drain-current-bias')[1] / 0.1:.3f} mA", f"{redis.redis_ts.get('status:feedline1:hemt:drain-voltage-bias')[1]:.3f} V"],
            ["2", f"{redis.redis_ts.get('status:feedline2:hemt:gate-voltage-bias')[1]:.3f} V", f"{redis.redis_ts.get('status:feedline1:hemt:drain-current-bias')[1] / 0.1:.3f} mA", f"{redis.redis_ts.get('status:feedline1:hemt:drain-voltage-bias')[1]:.3f} V"],
            ["3", f"{redis.redis_ts.get('status:feedline3:hemt:gate-voltage-bias')[1]:.3f} V", f"{redis.redis_ts.get('status:feedline1:hemt:drain-current-bias')[1] / 0.1:.3f} mA", f"{redis.redis_ts.get('status:feedline1:hemt:drain-voltage-bias')[1]:.3f} V"],
            ["4", f"{redis.redis_ts.get('status:feedline4:hemt:gate-voltage-bias')[1]:.3f} V", f"{redis.redis_ts.get('status:feedline1:hemt:drain-current-bias')[1] / 0.1:.3f} mA", f"{redis.redis_ts.get('status:feedline1:hemt:drain-voltage-bias')[1]:.3f} V"],
            ["5", f"{redis.redis_ts.get('status:feedline5:hemt:gate-voltage-bias')[1]:.3f} V", f"{redis.redis_ts.get('status:feedline1:hemt:drain-current-bias')[1] / 0.1:.3f} mA", f"{redis.redis_ts.get('status:feedline1:hemt:drain-voltage-bias')[1]:.3f} V"]]

    magnet_vals = [['Magnet current', f"{redis.redis_ts.get('status:highcurrentboard:current')[1]:.3f} A"], # TODO: Add a 'predicted voltage' value (based on SIM960 output * conversion factor)?
                   ['SIM960 control voltage', f"{redis.redis_ts.get('status:device:sim960:hcfet-control-voltage')[1]:.3f} V"],
                   ['Control Mode', redis.read(['device-settings:sim960:mode'])['device-settings:sim960:mode']],
                   ['Heat Switch', redis.read(['status:heatswitch'])['status:heatswitch']]]  # TODO: Allow flipping here?

    return render_template('index.html', form=form, table_headers=therm_headers, table_data=therm_data,
                                 hemt_tableh=hemt_headers, hemt_tablev=hemt_vals, current_tableh=magnet_vals)


@app.route('/settings', methods=['GET', 'POST'])
def settings():
    form = SettingForm()
    if request.method == 'POST':
        # TODO: There must be a different better way to do this (matching redis keys to field labels)
        keys = ['device-settings:sim960:mode', 'device-settings:sim960:setpoint-mode',
                'device-settings:sim960:setpoint-ramp-enable', 'device-settings:sim960:pid-p:enabled',
                'device-settings:sim960:pid-i:enabled', 'device-settings:sim960:pid-d:enabled',
                'device-settings:sim921:resistance-range', 'device-settings:sim921:excitation-value',
                'device-settings:sim921:excitation-mode', 'device-settings:sim921:time-constant',
                'device-settings:sim921:output-mode', 'device-settings:sim921:curve-number']
        desired_vals = form.data
        current_vals = redis.read(keys)
        for k1, k2, v1, v2 in zip(current_vals.keys(), desired_vals.keys(), current_vals.values(), desired_vals.values()):
            if v1 != v2:
                print(f"Change {k1} from {v1} to {v2}")
                redis.publish(k1, v2)

        return redirect(url_for('settings'))
    else:
        return render_template('settings.html', title='Settings', form=form)


@app.route('/info', methods=['GET', 'POST'])
def info():
    form = FlaskForm()
    return render_template('info.html', title='Info', form=form)


def make_choices(key):
    current_value = redis.read([key])[key]
    rest = list(COMMAND_DICT[key]['vals'].keys())
    choice = [current_value]
    rest.remove(current_value)
    for i in rest:
        choice.append(i)
    print(choice)
    return choice


class SettingForm(FlaskForm):
    sim960_mode = SelectField('SIM960 Mode', choices=make_choices('device-settings:sim960:mode'))
    sim960_setpoint_mode = SelectField('SIM960 Setpoint Mode', choices=make_choices('device-settings:sim960:setpoint-mode'))
    sim960_enable_setpoint_ramp = SelectField('SIM960 Internal Setpoint Ramp Enable', choices=make_choices('device-settings:sim960:setpoint-ramp-enable'))
    sim960_p_on = SelectField('SIM960 PID: P Enabled', choices=make_choices('device-settings:sim960:pid-p:enabled'))
    sim960_i_on = SelectField('SIM960 PID: I Enabled', choices=make_choices('device-settings:sim960:pid-i:enabled'))
    sim960_d_on = SelectField('SIM960 PID: D Enabled', choices=make_choices('device-settings:sim960:pid-d:enabled'))

    sim921_resistance_range = SelectField('SIM921 Resistance Range', choices=make_choices('device-settings:sim921:resistance-range'))
    sim921_excitation_val = SelectField('SIM921 Excitation Value', choices=make_choices('device-settings:sim921:excitation-value'))
    sim921_excitation_mode = SelectField('SIM921 Excitation Mode', choices=make_choices('device-settings:sim921:excitation-mode'))
    sim921_time_constant = SelectField('SIM921 Time Constant', choices=make_choices('device-settings:sim921:time-constant'))
    sim921_output_mode = SelectField('SIM921 Output Mode', choices=make_choices('device-settings:sim921:output-mode'))
    sim921_curve = SelectField('SIM921 Calibration Curve', choices=make_choices('device-settings:sim921:curve-number'))

    submit = SubmitField('Update', [DataRequired()])


if __name__ == "__main__":

    app.debug=True
    app.run(port=8000)