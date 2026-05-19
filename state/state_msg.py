from state import BaseState


class SendMsgState(BaseState):
    stateName = 'SendMsgState'

    def __init__(self, response: str, **kwargs):
        super().__init__()
        self.response = response

    def Enter(self, owner):
        super().Enter(owner)

    def Execute(self, owner):
        print(f"请回答问题：{self.response}")
        owner.remove_state(self)
