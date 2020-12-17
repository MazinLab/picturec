import flask
from flask_wtf import FlaskForm
from picturec.frontend.config import Config

app = flask.Flask(__name__)
app.config.from_object(Config)


@app.route('/', methods=['GET', 'POST'])
@app.route('/main', methods=['GET', 'POST'])
def index():
    form = FlaskForm()
    return flask.render_template('index.html', form=form)


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
