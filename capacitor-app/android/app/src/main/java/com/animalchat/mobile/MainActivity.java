package com.animalchat.mobile;

import android.os.Bundle;
import com.google.firebase.FirebaseApp;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        FirebaseApp.initializeApp(this);
        registerPlugin(PushSupportPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
