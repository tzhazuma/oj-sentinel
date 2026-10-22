// Thin Kotlin WebView client for the OJ Sentinel desktop/server.
// No AndroidX and no third-party UI libraries: the whole UI uses framework
// widgets, so the debug APK builds offline against the Android SDK alone.
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.ojsentinel.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.ojsentinel.app"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"
        // The OJ Sentinel server runs on a computer (desktop). 10.0.2.2 is the
        // Android emulator alias for the host loopback; the default remains the
        // documented 127.0.0.1 and is editable in the settings screen.
        buildConfigField("String", "DEFAULT_SERVER_URL", "\"http://127.0.0.1:8765\"")
    }

    buildFeatures {
        buildConfig = true
    }

    buildTypes {
        debug {
            isMinifyEnabled = false
            applicationIdSuffix = ""
        }
        release {
            // Debug-signed for local use; see docs/ANDROID.md for release signing.
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }
}

dependencies {
    // Only the Kotlin standard library; everything else is part of Android.
    implementation("org.jetbrains.kotlin:kotlin-stdlib:2.1.0")
}
