import flask
from flask_wtf import FlaskForm
from picturec.frontend.config import Config
import numpy as np

app = flask.Flask(__name__)
app.config.from_object(Config)


@app.route('/', methods=['GET', 'POST'])
@app.route('/main', methods=['GET', 'POST'])
def index():
    form = FlaskForm()
    therm_headers = ['Thermometer', 'Value']
    therm_data = [['Device', f"{np.random.uniform(.9,.1):.2f}"],
                  ['LHe Tank', f"{np.random.uniform(4.1,4.3):.2f}"],
                  ['LN2 Tank', f"{np.random.uniform(76.7, 77.3):.2f}"]]

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
    form = FlaskForm()
    return flask.render_template('settings.html', title='Settings', form=form)


@app.route('/info', methods=['GET', 'POST'])
def info():
    form = FlaskForm()
    return flask.render_template('info.html', title='Info', form=form)


if __name__ == "__main__":
    app.debug=True
    app.run()
