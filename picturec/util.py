import logging


def setup_logging():
    logging.basicConfig(level=logging.DEBUG)  # Note that ultimately this is going to need to change. As written I suspect
    # all log messages will appear from "__main__" instead of showing up from "picturec.currentduinoAgent.Currentduino"
    # TODO: Logging for a package
