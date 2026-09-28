import sys
import re

filepath = r'd:\aegisMind\apps\lens\src\components\Connectors.tsx'
with open(filepath, 'r') as f:
    content = f.read()

# Remove unused const result = await triggerSync(connector.name);
content = content.replace("const result = await triggerSync(connector.name);", "await triggerSync(connector.name);")

with open(filepath, 'w') as f:
    f.write(content)
