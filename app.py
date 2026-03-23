import os,threading
from datetime import datetime
from flask import Flask,request,Response,jsonify
from twilio.twiml.voice_response import VoiceResponse,Gather
from twilio.rest import Client
from groq import Groq
app=Flask(__name__)
TWILIO_SID=os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN=os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER=os.getenv("TWILIO_PHONE_NUMBER")
MASTER_NUMBER=os.getenv("MASTER_PHONE_NUMBER")
BASE_URL=os.getenv("BASE_URL","").rstrip("/")
GROQ_API_KEY=os.getenv("GROQ_API_KEY")
twilio_client=Client(TWILIO_SID,TWILIO_TOKEN)
groq_client=Groq(api_key=GROQ_API_KEY)
active_calls={}
call_log=[]
PROMPT_EN="You are Xnbok, warm AI voice secretary. Master is unavailable. Apologize, take message, get caller name, confirm message, say goodbye. Keep replies 1-2 sentences. End final goodbye with exactly: [DONE: name|message]"
PROMPT_BN="আপনি Xnbok, মাস্টারের AI সেক্রেটারি। মাস্টার ব্যস্ত। ক্ষমা চান, বার্তা নিন, নাম জানুন, নিশ্চিত করুন, বিদায় জানান। ১-২ বাক্যে উত্তর দিন। শেষ বিদায়ে লিখুন: [DONE: নাম|বার্তা]"
def is_bn(t):
    return any("\u0980"<=c<="\u09FF" for c in (t or ""))
def say(node,text,lang):
    if lang=="bn":
        node.say(text,language="bn-IN",voice="Google.bn-IN-Standard-A")
    else:
        node.say(text,language="en-IN",voice="Polly.Aditi")
def ai(msgs,lang):
    r=groq_client.chat.completions.create(model="llama3-70b-8192",messages=[{"role":"system","content":PROMPT_BN if lang=="bn" else PROMPT_EN}]+msgs,max_tokens=150,temperature=0.75)
    return r.choices[0].message.content.strip()
def parse(text):
    if "[DONE:" not in text:
        return text,None,None
    try:
        s=text.index("[DONE:")+6
        e=text.index("]",s)
        p=text[s:e].strip().split("|",1)
        return text[:text.index("[DONE:")].strip(),p[0].strip(),p[1].strip() if len(p)>1 else "No message"
    except:
        return text,"Unknown","Unspecified"
def sms(body):
    def go():
        try:
            twilio_client.messages.create(body=body,from_=TWILIO_NUMBER,to=MASTER_NUMBER)
        except Exception as e:
            print(e)
    threading.Thread(target=go,daemon=True).start()
@app.route("/incoming",methods=["POST"])
def incoming():
    sid=request.form["CallSid"]
    active_calls[sid]={"caller":request.form.get("From","?"),"start_time":datetime.now(),"messages":[],"lang":"en","message":None,"recording_url":None}
    r=VoiceResponse()
    d=r.dial(timeout=60,action=f"{BASE_URL}/no-answer",record="record-from-ringing-dual",recording_status_callback=f"{BASE_URL}/recording")
    d.number(MASTER_NUMBER)
    return Response(str(r),mimetype="text/xml")
@app.route("/no-answer",methods=["POST"])
def no_answer():
    sid=request.form["CallSid"]
    status=request.form.get("DialCallStatus","no-answer")
    r=VoiceResponse()
    if status=="completed":
        return Response(str(r),mimetype="text/xml")
    g=r.gather(input="speech",timeout=5,speech_timeout="auto",language="en-IN",action=f"{BASE_URL}/first/{sid}",method="POST")
    g.say("Hello I am Xnbok, my master's assistant. This call is recorded. Please speak English or Bengali.",language="en-IN",voice="Polly.Aditi")
    r.redirect(f"{BASE_URL}/first/{sid}?SpeechResult=hello")
    return Response(str(r),mimetype="text/xml")
@app.route("/first/<sid>",methods=["POST"])
def first(sid):
    speech=request.form.get("SpeechResult","hello")
    lang="bn" if is_bn(speech) else "en"
    d=active_calls.get(sid,{})
    d["lang"]=lang
    g_text="নমস্কার! আমি Xnbok। মাস্টার এখন ব্যস্ত। কোনো বার্তা দিতে চান?" if lang=="bn" else "Hello! I'm Xnbok. My master is unavailable. Would you like to leave a message?"
    msgs=d.get("messages",[])
    msgs.append({"role":"assistant","content":g_text})
    if speech and speech!="hello":
        msgs.append({"role":"user","content":speech})
    d["messages"]=msgs
    r=VoiceResponse()
    tl="bn-IN" if lang=="bn" else "en-IN"
    g=r.gather(input="speech",timeout=6,speech_timeout="auto",language=tl,action=f"{BASE_URL}/turn/{sid}",method="POST")
    say(g,g_text,lang)
    r.redirect(f"{BASE_URL}/turn/{sid}?SpeechResult=")
    return Response(str(r),mimetype="text/xml")
@app.route("/turn/<sid>",methods=["POST"])
def turn(sid):
    speech=request.form.get("SpeechResult","")
    d=active_calls.get(sid,{"messages":[],"lang":"en"})
    if is_bn(speech):
        d["lang"]="bn"
    lang=d.get("lang","en")
    msgs=d.get("messages",[])
    if speech:
        msgs.append({"role":"user","content":speech})
    try:
        raw=ai(msgs,lang)
    except Exception as e:
        print(e)
        raw="Sorry, technical issue. Goodbye! [DONE: Unknown|Error]"
    clean,name,msg=parse(raw)
    msgs.append({"role":"assistant","content":clean or raw})
    d["messages"]=msgs
    r=VoiceResponse()
    if name is not None:
        if clean:
            say(r,clean,lang)
        r.hangup()
        d["message"]=msg
        d["caller_name"]=name
        call_log.append(dict(d))
        t=d["start_time"].strftime("%d %b %Y, %I:%M %p")
        sms(f"Missed Call - Xnbok\nName: {name}\nNumber: {d.get('caller','?')}\nTime: {t}\nMessage: {msg}")
    else:
        tl="bn-IN" if lang=="bn" else "en-IN"
        g=r.gather(input="speech",timeout=7,speech_timeout="auto",language=tl,action=f"{BASE_URL}/turn/{sid}",method="POST")
        say(g,clean,lang)
        say(r,"ধন্যবাদ বিদায়" if lang=="bn" else "Thank you goodbye",lang)
        r.hangup()
    return Response(str(r),mimetype="text/xml")
@app.route("/recording",methods=["POST"])
def recording():
    sid=request.form.get("CallSid","")
    if sid in active_calls:
        active_calls[sid]["recording_url"]=request.form.get("RecordingUrl","")
    return Response("",status=204)
@app.route("/send-log",methods=["GET","POST"])
def send_log():
    if not call_log:
        sms("Xnbok: No missed calls.")
        return jsonify({"status":"no calls"})
    lines=["Xnbok Call Log"]
    for i,c in enumerate(call_log,1):
        lines.append(f"{i}. {c.get('caller_name','?')} ({c.get('caller','?')}) {c['start_time'].strftime('%d %b %I:%M%p')} - {c.get('message','')}")
    sms("\n".join(lines))
    call_log.clear()
    return jsonify({"status":"sent"})
@app.route("/health")
def health():
    return jsonify({"agent":"Xnbok","status":"live"})
if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT",5000)),debug=False)
