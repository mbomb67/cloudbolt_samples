"""
This is a working sample Generated Parameter Options CloudBolt plug-in for you to start with.
The get_options_list method is required, but you can change all the code within it.
See the "Generated Parameter Options" section of the docs for more info and the CloudBolt forge
for more examples: https://github.com/CloudBoltSoftware/cloudbolt-forge/tree/master/actions/cloudbolt_plugins
"""

   def get_options_list(field, control_value=None, **kwargs):
       """
       A plug-in for regenerating 'Species' options based on the value of the controlling field 'Ansible Tower'
       field: This is the dependent field.
       control_value: The value entered on the form for the controlling field (Ansible Tower). This value determines the options
       that will be displayed for "field"
       """
       options = []

       if control_value == 'Acer':
           options = [
               ('rubrum', 'rubrum'),
               ('saccharinum', 'saccharinum'),
               ('macrophyllum', 'macrophyllum')
           ]
       elif control_value == 'Sequoia':
           options = [
               ('magnifica', 'magnifica'),
               ('langsdorfii', 'langsdorfii'),
               ('giganteum', 'giganteum')
           ]
       return dict(
           options=options,
           override=True
       )
