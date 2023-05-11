from django import forms
from common.forms import C2Form
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


class RunEvaluationForm(C2Form):
    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        if self.request is not None:
            if self.request.method == 'GET':
                policies = kwargs.pop('policies')
                if policies:
                    choices = []
                    for policy in policies:
                        p_id = ":".join(policy.split(':')[0:2])
                        p_name = policy.split(':')[2]
                        choices.append((p_id, p_name))
                    self.base_fields['policy'].choices = choices
        super().__init__(*args, **kwargs)

    policy = forms.ChoiceField(
        label="Policy",
        required=True,
    )
