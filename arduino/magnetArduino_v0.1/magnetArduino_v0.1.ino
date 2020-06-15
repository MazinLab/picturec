float R1 = 11790;
float R2 = 11690;

byte ledPin = 13;   // the onboard LED
byte openPin = 10;
byte closePin = 11;

//===============

/* Pin map
 * A5 = Magnet Voltage (1 V/A)
 * D10 = Open Pin (Heat Switch)
 * D11 = Close Pin (Heat Switch)
 */

 //===================

void setup() {
  Serial.begin(115200);
  pinMode(openPin, OUTPUT);
  pinMode(closePin, OUTPUT);
  pinMode(ledPin, OUTPUT);
  digitalWrite(ledPin, HIGH);
  delay(100);
  digitalWrite(ledPin, LOW);
  delay(100);
  digitalWrite(ledPin, HIGH);
}

//====================================

void openHeatSwitch() {
  digitalWrite(openPin, HIGH);
  delay(50);
  digitalWrite(openPin, LOW);
}

void closeHeatSwitch() {
  digitalWrite(closePin, HIGH);
  delay(50);
  digitalWrite(closePin, LOW);
}

void convertVoltageToCurrent() {
  float val;
  float voltage;
  val = analogRead(5);
  voltage = (val * (5.0/1023.0) * ((R1+R2) / R2));
}

//====================================

void loop() {
  char confirm;
  if (Serial.available()>0) {
    while (Serial.available()) {
      char x = Serial.read();
      confirm = x;
    }
    if (String(confirm)=="?") {
      Serial.print(" ");
      Serial.print(analogRead(5));
    }
    else if (String(confirm)=="o") {
      openHeatSwitch();
    }
    else if (String(confirm)=="c") {
      closeHeatSwitch();
    }
    Serial.print(" ");
    Serial.print(confirm);
    Serial.println();
  }
}
