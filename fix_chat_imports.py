import sys
import re

filepath = r'd:\aegisMind\apps\lens\src\components\Chat.tsx'
with open(filepath, 'r') as f:
    content = f.read()

repl = """import {
  SendHorizontal,
  Bot,
  User,
  Shield,
  FileText,
  Paperclip,
  X,
  StopCircle,
  Link2,
  ExternalLink,
  Cpu,
  Lock,
  MessageSquare,
  CornerDownRight,
  Database,
  Github,
  Mail,
  Check,
  Plus,
  Square,
  Send
} from "lucide-react";"""

content = re.sub(r'import \{[^}]+\} from "lucide-react";', repl, content)

with open(filepath, 'w') as f:
    f.write(content)
