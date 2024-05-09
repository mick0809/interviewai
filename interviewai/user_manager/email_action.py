from interviewai.user_manager.mail import LoopsManager, LoopsEventName, LoopsUserGroup
from interviewai.user_manager.clerkapi import get_user_by_id
from datetime import datetime, timedelta

######################################################################################
# Make sure if testing locally use env="dev" and if use it on production use env="prod#
######################################################################################

loops = LoopsManager()

EVENT_PERIODS = {
    LoopsEventName.Non_LOGIN_7_DAYS.value: 7,
    LoopsEventName.Non_LOGIN_30_DAYS.value: 30,
    LoopsEventName.CONV_REMINDER_14_DAYS.value: 14,
    LoopsEventName.CONV_REMINDER_30_DAYS.value: 30,
}


def fetch_batches(last_doc=None):
    users_ref = loops.db.collection('users')
    query = users_ref.order_by('__name__').limit(50)  # Adjust the limit as needed
    if last_doc:
        query = query.start_after(last_doc)

    return query.stream()


def send_periodic_event(event_name, last_doc_id=None):
    if last_doc_id is None:
        last_doc = None
    else:
        last_doc = loops.db.collection('users').document(last_doc_id).get()
    """ Send non login event to all users who have not login for 7 days or 30 days"""
    while True:
        batch = fetch_batches(last_doc)
        docs = list(batch)
        if not docs:
            break

        for user in docs:
            try:
                user_id = user.id
                user_info = get_user_by_id(user_id)
                if user_info.get("errors") is not None:
                    print(f"{user_id} does not exist in clerk")
                    continue
                email = user_info["email_addresses"][0]["email_address"]
                last_sign_in = user_info['last_sign_in_at']
                created_at = user_info['created_at']
                if last_sign_in:
                    cutoff = last_sign_in
                elif created_at:
                    cutoff = created_at
                cutoff_date = datetime.utcfromtimestamp(cutoff / 1000)
                current_date = datetime.utcnow()
                period = EVENT_PERIODS.get(event_name)
                if period is None:
                    print("Event name not exist")
                    return
                if current_date - cutoff_date > timedelta(days=period):
                    response_list = loops.find_contact(email)
                    if len(response_list) != 0:
                        response = loops.send_event(user_id, event_name)
                        print(response, f"send email to {user_id}")
                    else:
                        print(f"{email} does not exist in loops: user_id {user_id}")
                else:
                    print(f"{user_id} Account login within 7 days, don't send email")
            except Exception as e:
                print(f"Error: {e}")
                continue
        last_doc = docs[-1]


def test_event():
    user_id = "user_2UMskt7Q5PRYS7q3LpRPbr4bJoF"
    user_info = get_user_by_id(user_id)
    email = user_info["email_addresses"][0]["email_address"]
    first_name = user_info["first_name"]
    last_sign_in = user_info['last_sign_in_at']
    created_at = user_info['created_at']
    if last_sign_in:
        cutoff = last_sign_in
    elif created_at:
        cutoff = created_at
    cutoff_date = datetime.utcfromtimestamp(cutoff / 1000)
    current_date = datetime.utcnow()
    if current_date - cutoff_date > timedelta(days=7):
        response_list = loops.find_contact(email)
        if len(response_list) != 0:
            response = loops.send_event(user_id, LoopsEventName.Non_LOGIN_30_DAYS.value)
            print(response, "email sent")
        else:
            print("email not exist in loops")
    else:
        print("don't send email")

if __name__ == "__main__":
    # response = test_event()
    # note: this is a one time email, so don't worry about sending it again
    # user_id = "asdadsada"
    #send_periodic_event(LoopsEventName.Non_LOGIN_7_DAYS.value)
    #testing when a new email is added if in case there is a duplicated email, we will update the user group instead
    """ response = loops.create_contact("tothemoonhahaha@gmail.com","whatthe","heck",True,"user_2ep2tkRRsKX62zplFguT5KPpFya", LoopsUserGroup.NEW_SIGN_UP.value)
    print(response) """