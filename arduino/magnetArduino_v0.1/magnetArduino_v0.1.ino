float R1 = 11790;
float R2 = 11690;
float FIRMWARE_VERSION = 0.2;

byte ledPin = 13;   // the onboard LED
byte openPin = 10;
byte closePin = 11;
byte checkPin = 9;

//===============

/* Pin map
 * A5 = Magnet Voltage (1 V/A)
 * D9 = Check Open/Close Pin (Heat Switch)
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
  digitalWrite(ledPin, LOW);
  digitalWrite(ledPin, HIGH);
}

//====================================

void openHeatSwitch() {
//  Serial.print("writing open pin high");
  digitalWrite(openPin, HIGH);
  delay(50);
//  Serial.print("writing open pin low");
  digitalWrite(openPin, LOW);
}

void closeHeatSwitch() {
//  Serial.print("writing close pin high");
  digitalWrite(closePin, HIGH);
  delay(50);
//  Serial.print("writing close pin low");
  digitalWrite(closePin, LOW);
}

void checkClosed() {
//  if (digitalRead(checkPin) == LOW) {
//    return 'l';
//  }
//  else {
//    return 'h';
//  }
  return 'l';
}

void checkOpen() {
//  if (digitalRead(checkPin) == HIGH) {
//    return 'h';
//  }
//  else {
//    return 'l';
//  }
  return 'h';
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
    else if (String(confirm)=="v") {
      Serial.print(" ");
      Serial.print(FIRMWARE_VERSION);
    }
    else if (String(confirm)=="h" {
      Serial.print(" ");
      char check = checkOpen();
      Serial.print(check);
    }
    else if (String(confirm)=="l" {
      Serial.print(" ");
      char check = checkClosed();
      Serial.print(check);
    }
    Serial.print(" ");
    Serial.print(confirm);
    Serial.println();
  }
}
