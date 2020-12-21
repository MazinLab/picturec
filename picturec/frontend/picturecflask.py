import flask
from flask_wtf import FlaskForm
from flask import request
from wtforms import SelectField, SubmitField
from wtforms.validators import DataRequired
from picturec.frontend.config import Config
import numpy as np
from picturec.pcredis import PCRedis
from picturec.devices import COMMAND_DICT

app = flask.Flask(__name__)
app.config.from_object(Config)
REDIS_DB = 0
redis = PCRedis(host='127.0.0.1', port=6379, db=REDIS_DB)


@app.route('/', methods=['GET', 'POST'])
@app.route('/main', methods=['GET', 'POST'])
def index():
    form = FlaskForm()
    therm_headers = ['Thermometer', 'Value']
    therm_data = [['Device', f"{np.random.uniform(97, 103):.3f} mK"],
                  ['LHe Tank', f"{np.random.uniform(4.1,4.3):.2f} K"],
                  ['LN2 Tank', f"{np.random.uniform(76.7, 77.3):.2f} K"]]

    hemt_headers = ['Hemt', 'Vg', 'Id', 'Vd']
    hemt_vals = [["1", f"{np.random.uniform(.05, .15):.3f} V", f"{np.random.uniform(6, 12):.3f} mA", f"{np.random.uniform(.6, .8):.3f} V"],
            ["2", f"{np.random.uniform(-1.4, -1.2):.3f} V", f"{np.random.uniform(7, 13):.3f} mA", f"{np.random.uniform(.5, .7):.3f} V"],
            ["3", f"{np.random.uniform(-1.21, -1.01):.3f} V", f"{np.random.uniform(7, 13):.3f} mA", f"{np.random.uniform(.5, .7):.3f} V"],
            ["4", f"{np.random.uniform(-1.31, -1.11):.3f} V", f"{np.random.uniform(7, 13):.3f} mA", f"{np.random.uniform(.5, .7):.3f} V"],
            ["5", f"{np.random.uniform(-1.11, -.91):.3f} V", f"{np.random.uniform(10, 16):.3f} mA", f"{np.random.uniform(.4, .6):.3f} V"]]

    magnet_vals = [['Magnet current', f"{np.random.uniform(0,.01):.3f} A"],
                      ['SIM960 control voltage', f"{np.random.uniform(0,.001):.3f} V"],
                      ['Control Mode', "Manual"],
                      ['Heat Switch', 'Closed']]
    return flask.render_template('index.html', form=form, table_headers=therm_headers, table_data=therm_data,
                                 hemt_tableh=hemt_headers, hemt_tablev=hemt_vals, current_tableh=magnet_vals)


@app.route('/settings', methods=['GET', 'POST'])
def settings():
    form = SettingForm()
    if request.method == 'POST':
        print('validated')
    else:
        print('unvalidated')
    return flask.render_template('settings.html', title='Settings', form=form)


@app.route('/info', methods=['GET', 'POST'])
def info():
    form = FlaskForm()
    return flask.render_template('info.html', title='Info', form=form)


def make_choices(key):
    current_value = redis.read([key])
    rest = list(COMMAND_DICT[key]['vals'].keys())
    choice = [current_value[key]]
    rest.remove(choice[0])
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

    submit = SubmitField('Submit', [DataRequired()])




if __name__ == "__main__":

    app.debug=True
    app.run()
