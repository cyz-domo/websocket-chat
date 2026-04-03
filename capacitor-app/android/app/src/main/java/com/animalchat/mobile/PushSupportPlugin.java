package com.animalchat.mobile;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.google.firebase.FirebaseApp;

@CapacitorPlugin(name = "PushSupport")
public class PushSupportPlugin extends Plugin {
    @PluginMethod
    public void getStatus(PluginCall call) {
        JSObject result = new JSObject();

        try {
            FirebaseApp firebaseApp = FirebaseApp.initializeApp(getContext());
            result.put("firebaseConfigured", firebaseApp != null);
        } catch (Exception exception) {
            result.put("firebaseConfigured", false);
            result.put("error", exception.getMessage());
        }

        call.resolve(result);
    }
}
