const byte numChars = 64;
char receivedChars[numChars];

boolean newData = false;

byte ledPin = 13;   // the onboard LED
byte openPin = 10
byte closePin = 11

//===============

/* Pin map
 * A5 = Magnet Voltage (1 V/A)
 */

 //===================

void setup() {
  Serial.begin(115200);
  pinMode(openPin, OUTPUT);
  pinMode(closePin, OUTPUT);
  pinMode(ledPin, OUTPUT);
  digitalWrite(ledPin, HIGH);
  delay(200);
  digitalWrite(ledPin, LOW);
  delay(200);
  digitalWrite(ledPin, HIGH);

  Serial.println("<Arduino is ready>");
}

void recvWithStartEndMarkers() {
    static boolean recvInProgress = false;
    static byte ndx = 0;
    char startMarker = '<';
    char endMarker = '>';
    char rc;

    while (Serial.available() > 0 && newData == false) {
        rc = Serial.read();

        if (recvInProgress == true) {
            if (rc != endMarker) {
                receivedChars[ndx] = rc;
                ndx++;
                if (ndx >= numChars) {
                    ndx = numChars - 1;
                }
            }
            else {
                receivedChars[ndx] = '\0'; // terminate the string
                recvInProgress = false;
                ndx = 0;
                newData = true;
            }
        }

        else if (rc == startMarker) {
            recvInProgress = true;
        }
    }
}

//====================================

void replyToPython() {
    if (newData == true) {
        unsigned int i;
        unsigned int sensorValue[6];
        float voltage[6];
        float R1 = 11790;
        float R2 = 11690;

        Serial.print("<");
        if(String(receivedChars)=="current" || String(receivedChars)=="i" || String(receivedChars)=="I"){
            sensorValue[0] = analogRead(A0);
            delay(1);
            sensorValue[1] = analogRead(A1);
            delay(1);
            sensorValue[2] = analogRead(A2);
            delay(1);
            sensorValue[3] = analogRead(A3);
            delay(1);
            sensorValue[4] = analogRead(A4);
            delay(1);
            sensorValue[5] = analogRead(A5);  
            delay(1);

            for (i=5;i<6;i++) {
              if (i == 5) {
                voltage[i] = (sensorValue[i]*(5.0/1023.0)*((R1+R2)/R2));
              }
              else {
                voltage[i] = (sensorValue[i]*(5.0/1023.0));
              }
              Serial.print(i); Serial.print(" "); Serial.print(voltage[i]); Serial.print(" ");
            }
        else if (String(receivedChars)=="open" || String(receivedChars)=="o") {
          digitalWrite(openPin, HIGH);
          delay(50);
          digitalWrite(openPin, LOW);
          }
        else if (String(receivedChars)=="close" || String(receivedChars)=="c") {
          digitalWrite(closePin, HIGH);
          delay(50);
          digitalWrite(closePin, LOW);
          }
        else{
          Serial.print("INVALID COMMAND "); Serial.print(receivedChars);
          }
        Serial.print('>');
            // change the state of the LED everytime a reply is sent
        digitalWrite(ledPin, ! digitalRead(ledPin));
        newData = false;
      }
    }
//====================================

void loop() {
    recvWithStartEndMarkers();
    replyToPython();
}
