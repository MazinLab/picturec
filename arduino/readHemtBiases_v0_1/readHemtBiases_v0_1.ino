
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
  delay(20);
  digitalWrite(ledPin, LOW);
  delay(20);
  digitalWrite(ledPin, HIGH);
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
//                digitalWrite(ledPin, ! digitalRead(ledPin));            
            }
        }

        else if (rc == startMarker) {
            recvInProgress = true;
            digitalWrite(ledPin, ! digitalRead(ledPin));
        }
    }
}

//====================================

void replyToPython() {
    if (newData == true) {
//        digitalWrite(ledPin, ! digitalRead(ledPin));
        unsigned int i;
        unsigned int sensorValue[16];
        float voltage[16];
        float R1 = 5000;
        float R2 = 5000;

        Serial.print("<");
        if(String(receivedChars)=="hemt" || String(receivedChars)=="all"){
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
            sensorValue[6] = analogRead(A6);
            delay(1);
            sensorValue[7] = analogRead(A7);  
            delay(1);
            sensorValue[8] = analogRead(A8);  
            delay(1);
            sensorValue[9] = analogRead(A9);
            delay(1);
            sensorValue[10] = analogRead(A10);
            delay(1);
            sensorValue[11] = analogRead(A11);  
            delay(1);
            sensorValue[12] = analogRead(A12);
            delay(1);
            sensorValue[13] = analogRead(A13);  
            delay(1);
            sensorValue[14] = analogRead(A14);  
            delay(1);
            sensorValue[15] = analogRead(A15); 
            for (i=1;i<16;i++) {
              if(i==1 || i==4 || i==7 || i==10 || i==13){
                voltage[i] = ((sensorValue[i]*(5.0/1023.0)) - 5);
              }
              else {
                voltage[i] = sensorValue[i]*(5.0/1023.0); 
              }
              Serial.print(i); Serial.print(" "); Serial.print(voltage[i]); Serial.print(" ");
            }
        }
        else if(String(receivedChars)=="hemt1" || String(receivedChars)=="1"){
            sensorValue[13] = analogRead(A13);  
            delay(1);
            sensorValue[14] = analogRead(A14);  
            delay(1);
            sensorValue[15] = analogRead(A15); 
            for (i=13;i<16;i++) {
              if(i==13){
                voltage[i] = 2 * ((sensorValue[i]*(5.0/1023.0)) - 2.5);
              }
              else {
                voltage[i] = sensorValue[i]*(5.0/1023.0); 
              }
              
              Serial.print(i); Serial.print(" "); Serial.print(voltage[i]); Serial.print(" ");
            }
        }
        else if(String(receivedChars)=="hemt2" || String(receivedChars)=="2"){
            sensorValue[10] = analogRead(A10);  
            delay(1);
            sensorValue[11] = analogRead(A11);  
            delay(1);
            sensorValue[12] = analogRead(A12); 
            for (i=10;i<14;i++) {
              if(i==10){
                voltage[i] = 2 * ((sensorValue[i]*(5.0/1023.0)) - 2.5);
              }
              else {
                voltage[i] = sensorValue[i]*(5.0/1023.0); 
              }
              
              Serial.print(i); Serial.print(" "); Serial.print(voltage[i]); Serial.print(" ");
            }
        }
        else if(String(receivedChars)=="hemt3" || String(receivedChars)=="3"){
            sensorValue[7] = analogRead(A7);  
            delay(1);
            sensorValue[8] = analogRead(A8);  
            delay(1);
            sensorValue[9] = analogRead(A9); 
            for (i=7;i<10;i++) {
              if(i==7){
                voltage[i] = 2 * ((sensorValue[i]*(5.0/1023.0)) - 2.5);
              }
              else {
                voltage[i] = sensorValue[i]*(5.0/1023.0); 
              }
              
              Serial.print(i); Serial.print(" "); Serial.print(voltage[i]); Serial.print(" ");
            }
        }
        else if(String(receivedChars)=="hemt4" || String(receivedChars)=="4"){
            sensorValue[4] = analogRead(A4);  
            delay(1);
            sensorValue[5] = analogRead(A5);  
            delay(1);
            sensorValue[6] = analogRead(A6); 
            for (i=4;i<7;i++) {
              if(i==13){
                voltage[i] = 2 * ((sensorValue[i]*(5.0/1023.0)) - 2.5);
              }
              else {
                voltage[i] = sensorValue[i]*(5.0/1023.0); 
              }
              
              Serial.print(i); Serial.print(" "); Serial.print(voltage[i]); Serial.print(" ");
            }
        }
        else if(String(receivedChars)=="hemt5" || String(receivedChars)=="5"){
            sensorValue[1] = analogRead(A1);  
            delay(1);
            sensorValue[2] = analogRead(A2);  
            delay(1);
            sensorValue[3] = analogRead(A3); 
            for (i=1;i<4;i++) {
              if(i==1){
                voltage[i] = 2 * ((sensorValue[i]*(5.0/1023.0)) - 2.5);
              }
              else {
                voltage[i] = sensorValue[i]*(5.0/1023.0); 
              }
              
              Serial.print(i); Serial.print(" "); Serial.print(voltage[i]); Serial.print(" ");
            }
        }
        else if(String(receivedChars)=="ping" || String(receivedChars)=="!"){
          Serial.print("Arduino is ready");
        }
        else{
          Serial.print("INVALID COMMAND "); Serial.print(receivedChars);
        }
        Serial.print('>');
            // change the state of the LED everytime a reply is sent
        newData = false;
      }
    }
//====================================

void loop() {
    recvWithStartEndMarkers();
    replyToPython();
}
