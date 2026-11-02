from krita import Krita
from .extension import SmartUIExtension

Krita.instance().addExtension(SmartUIExtension(Krita.instance()))
