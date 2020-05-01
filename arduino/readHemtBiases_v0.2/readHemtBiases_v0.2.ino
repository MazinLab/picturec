const byte numChars = 64;
char receivedChars[numChars];

boolean newData = false;

byte ledPin = 13;   // the onboard LED

//===============

/* HEMT-to-Analog Pin map
 * HEMT 1 : A13-A15
 * HEMT 2 : A10-A12
 * HEMT 3 : A7-A9
 * HEMT 4 : A4-A6
 * HEMT 5 : A1-A3 
 */

/* Pin-to-measurement
 * A1 = Vg, HEMT 5 
 * A2 = Id, HEMT 5
 * A3 = Vd, HEMT 5
 * A4 = Vg, HEMT 4 
 * A5 = Id, HEMT 4
 * A6 = Vd, HEMT 4
 * A7 = Vg, HEMT 3 
 * A8 = Id, HEMT 3
 * A9 = Vd, HEMT 3
 * A10 = Vg, HEMT 2 
 * A11 = Id, HEMT 2
 * A12 = Vd, HEMT 2
 * A13 = Vg, HEMT 1 
 * A14 = Id, HEMT 1
 * A15 = Vd, HEMT 1
 */

 //===================

void setup() {
  Serial.begin(115200);
  pinMode(ledPin, OUTPUT);
  digitalWrite(ledPin, HIGH);
  delay(100);
  digitalWrite(ledPin, LOW);
  delay(100);
  digitalWrite(ledPin, HIGH);
  Serial.println("started");
}

void receive() {
  static boolean recvInProgress = false;
  static byte ndx = 0;
  char rc;
  char startmarker = '<';
  char endmarker = '>' ;
  while (Serial.available() > 0 && newData == false) {
    rc = Serial.read();
    Serial.print(rc);

    if (recvInProgress == true){
      if (rc != endmarker) {
        receivedChars[ndx] = rc;
        ndx++;
        if (ndx >= numChars){
          ndx = numChars -1;
        }
      }
      else {
        receivedChars[ndx] = '\0';
        recvInProgress = false;
        ndx = 0;
        newData = true;
      }
    }
    else if (rc == startmarker) {
      recvInProgress = true;
      digitalWrite(ledPin, ! digitalRead(ledPin));
    }
  }
}

void reply() {
  if (newData == true) {
    Serial.print("<");
    Serial.print(receivedChars);
    Serial.println(">");
  }
  newData = false;
}

void loop() {
  receive();
  reply();
}
