import contextlib
import io
import time
import unittest
from unittest.mock import patch

from surface_map import js_parser, pr_js_signals

CANARY = "PRIVATE_TRACKING_EVENT_CANARY_57319"

# Representative full bootstrap versions, parsed only; never executed. Public
# tokens below are placeholders. A loader is NOT granted trust by its vendor
# name or download URL. Its opaque mutation remains explicit incomplete scope.
# https://support.google.com/tagmanager/answer/14847097
# https://developers.facebook.com/docs/meta-pixel/get-started/
# https://posthog.com/docs/libraries/js
# https://docs.mixpanel.com/docs/tracking-methods/sdks/javascript
# https://segment-docs.netlify.app/docs/connections/sources/catalog/libraries/website/javascript/quickstart/
VENDOR_LOADERS = {
    "gtm": ("""(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':
new Date().getTime(),event:'gtm.js'});var f=d.getElementsByTagName(s)[0],
j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=
'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);
})(window,document,'script','dataLayer','GTM-EXAMPLE');""", "gtag('event','purchase');", "gtag"),
    "meta": ("""!function(f,b,e,v,n,t,s){if(f.fbq)return;n=f.fbq=function(){n.callMethod?
n.callMethod.apply(n,arguments):n.queue.push(arguments)};if(!f._fbq)f._fbq=n;
n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;
t.src=v;s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}
(window,document,'script','https://connect.facebook.net/en_US/fbevents.js');""", "fbq('track','Purchase');", "fbq"),
    "posthog": ("""!function(t,e){var o,n,p,r;e.__SV||(window.posthog=e,e._i=[],e.init=function(i,s,a){function g(t,e){var o=e.split(".");2==o.length&&(t=t[o[0]],e=o[1]),t[e]=function(){t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}(p=t.createElement("script")).type="text/javascript",p.crossOrigin="anonymous",p.async=!0,p.src=s.api_host.replace(".i.posthog.com","-assets.i.posthog.com")+"/static/array.js",(r=t.getElementsByTagName("script")[0]).parentNode.insertBefore(p,r);var u=e;for(void 0!==a?u=e[a]=[]:a="posthog",u.people=u.people||[],Object.defineProperty(u,"toString",{configurable:!0,enumerable:!0,writable:!0,value:function(t){var e="posthog";return"posthog"!==a&&(e+="."+a),t||(e+=" (stub)"),e}}),Object.defineProperty(u.people,"toString",{configurable:!0,enumerable:!0,writable:!0,value:function(){return u.toString(1)+".people (stub)"}}),o="init capture register register_once register_for_session unregister unregister_for_session getFeatureFlag getFeatureFlagResult isFeatureEnabled reloadFeatureFlags updateEarlyAccessFeatureEnrollment getEarlyAccessFeatures on onFeatureFlags onSessionId getSurveys getActiveMatchingSurveys renderSurvey canRenderSurvey getNextSurveyStep identify setPersonProperties group resetGroups setPersonPropertiesForFlags resetPersonPropertiesForFlags setGroupPropertiesForFlags resetGroupPropertiesForFlags reset get_distinct_id getGroups get_session_id get_session_replay_url alias set_config startSessionRecording stopSessionRecording sessionRecordingStarted captureException loadToolbar get_property getSessionProperty createPersonProfile opt_in_capturing opt_out_capturing has_opted_in_capturing has_opted_out_capturing clear_opt_in_out_capturing debug".split(" "),n=0;n<o.length;n++)g(u,o[n]);e._i.push([i,s,a])},e.__SV=1)}(document,window.posthog||[]);
posthog.init('phc_example',{api_host:'https://us.i.posthog.com'});""", "posthog.capture('purchase');", "posthog"),
    "mixpanel": (r"""(function(f,b){if(!b.__SV){var e,g,i,h;window.mixpanel=b;b._i=[];b.init=function(e,f,c){function g(a,d){var b=d.split(".");2==b.length&&((a=a[b[0]]),(d=b[1]));a[d]=function(){a.push([d].concat(Array.prototype.slice.call(arguments,0)));};}var a=b;"undefined"!==typeof c?(a=b[c]=[]):(c="mixpanel");a.people=a.people||[];a.toString=function(a){var d="mixpanel";"mixpanel"!==c&&(d+="."+c);a||(d+=" (stub)");return d;};a.people.toString=function(){return a.toString(1)+".people (stub)";};i="disable time_event track track_pageview track_links track_forms track_with_groups add_group set_group remove_group register register_once alias unregister identify name_tag set_config reset opt_in_tracking opt_out_tracking has_opted_in_tracking has_opted_out_tracking clear_opt_in_out_tracking start_batch_senders people.set people.set_once people.unset people.increment people.append people.union people.track_charge people.clear_charges people.delete_user people.remove".split(" ");for(h=0;h<i.length;h++)g(a,i[h]);var j="set set_once union unset remove delete".split(" ");a.get_group=function(){function b(c){d[c]=function(){call2_args=arguments;call2=[c].concat(Array.prototype.slice.call(call2_args,0));a.push([e,call2]);};}for(var d={},e=["get_group"].concat(Array.prototype.slice.call(arguments,0)),c=0;c<j.length;c++)b(j[c]);return d;};b._i.push([e,f,c]);};b.__SV=1.2;e=f.createElement("script");e.type="text/javascript";e.async=!0;e.src="undefined"!==typeof MIXPANEL_CUSTOM_LIB_URL?MIXPANEL_CUSTOM_LIB_URL:"file:"===f.location.protocol&&"//cdn.mxpnl.com/libs/mixpanel-2-latest.min.js".match(/^\/\//)?"https://cdn.mxpnl.com/libs/mixpanel-2-latest.min.js":"//cdn.mxpnl.com/libs/mixpanel-2-latest.min.js";g=f.getElementsByTagName("script")[0];g.parentNode.insertBefore(e,g);}})(document,window.mixpanel||[]);
mixpanel.init('example');""", "mixpanel.track('purchase');", "mixpanel"),
    "segment": ("""!function(){var i="analytics",analytics=window[i]=window[i]||[];if(!analytics.initialize)if(analytics.invoked)window.console&&console.error&&console.error("Segment snippet included twice.");else{analytics.invoked=!0;analytics.methods=["trackSubmit","trackClick","trackLink","trackForm","pageview","identify","reset","group","track","ready","alias","debug","page","screen","once","off","on","addSourceMiddleware","addIntegrationMiddleware","setAnonymousId","addDestinationMiddleware","register"];analytics.factory=function(e){return function(){if(window[i].initialized)return window[i][e].apply(window[i],arguments);var n=Array.prototype.slice.call(arguments);if(["track","screen","alias","group","page","identify"].indexOf(e)>-1){var c=document.querySelector("link[rel='canonical']");n.push({__t:"bpc",c:c&&c.getAttribute("href")||void 0,p:location.pathname,u:location.href,s:location.search,t:document.title,r:document.referrer})}n.unshift(e);analytics.push(n);return analytics}};for(var n=0;n<analytics.methods.length;n++){var key=analytics.methods[n];analytics[key]=analytics.factory(key)}analytics.load=function(key,n){var t=document.createElement("script");t.type="text/javascript";t.async=!0;t.setAttribute("data-global-segment-analytics-key",i);t.src="https://cdn.segment.com/analytics.js/v1/"+key+"/analytics.min.js";var r=document.getElementsByTagName("script")[0];r.parentNode.insertBefore(t,r);analytics._loadOptions=n};analytics._writeKey="example";analytics.SNIPPET_VERSION="5.2.1";analytics.load("example");analytics.page();}}();""", "analytics.track('purchase');", "analytics"),
}


class BasicTrackingScopeTests(unittest.TestCase):
    def test_basic_mode_never_uses_regex_or_parser_for_tracking(self):
        with patch.object(pr_js_signals, "parse", side_effect=AssertionError("must not parse")):
            result = pr_js_signals.collect_javascript("posthog.capture('" + CANARY + "')", engine="basic")
        self.assertEqual(result["records"], [])
        self.assertEqual(result["engine"], "basic")
        self.assertIn("tracking_requires_enhanced_parser", result["limitations"])

    def test_auto_unavailable_is_explicit_and_does_not_parse(self):
        with patch.object(pr_js_signals, "available", return_value=(False, "missing")), \
                patch.object(pr_js_signals, "parse", side_effect=AssertionError("must not parse")):
            result = pr_js_signals.collect_javascript("gtag('event','x')", engine="auto")
        self.assertEqual(result["engine"], "unavailable")
        self.assertIn("tracking_requires_enhanced_parser", result["limitations"])

    def test_invalid_inputs_and_expired_budget_return_fixed_private_error(self):
        examples = [(None, {}), ("", {"source": CANARY}), ("", {"engine": CANARY}),
                    ("", {"kind": CANARY}), ("", {"deadline": float("inf")}),
                    ("", {"deadline": time.monotonic() - 1})]
        for text, options in examples:
            with self.subTest(options=list(options)):
                with self.assertRaises(ValueError) as caught:
                    pr_js_signals.collect_javascript(text, **options)
                self.assertEqual(str(caught.exception), pr_js_signals._ERROR)
                self.assertNotIn(CANARY, str(caught.exception))

    def test_oversized_source_rejected_before_parser(self):
        with patch.object(pr_js_signals, "MAX_SOURCE", 5), \
                patch.object(pr_js_signals, "parse", side_effect=AssertionError("must not parse")):
            with self.assertRaises(ValueError):
                pr_js_signals.collect_javascript(CANARY)


class TrackingSyntaxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ready, reason = js_parser.available()
        if not ready:
            raise unittest.SkipTest("Optional reviewed syntax parser unavailable: " + reason)

    @classmethod
    def tearDownClass(cls):
        js_parser.close()

    def collect(self, text, **options):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = pr_js_signals.collect_javascript(text, **options)
        self.assertEqual(out.getvalue() + err.getvalue(), "")
        for row in result["records"]:
            self.assertEqual(row["category"], "tracking")
            self.assertEqual(row["confidence"], "inferred")
            self.assertIn(row["assumptions"], [["unverified_global_tracking_sdk"], ["declared_tracking_import"], ["static_queue_shim_only"]])
            self.assertEqual(row["ambient"], row["assumptions"] != ["declared_tracking_import"])
            self.assertEqual(row["evidence"]["source"], options.get("source", "source_1"))
            self.assertRegex(row["evidence"]["location"], r"\Aline [1-9][0-9]*\Z")
        return result

    def identities(self, text, **options):
        return [row["identity"] for row in self.collect(text, **options)["records"]]

    def test_supported_ambient_sdk_calls_keep_only_private_event_identity(self):
        source = """
gtag('event', 'purchase', {secret: 'private-value'});
posthog.capture('signed_up', {email: 'private@example.invalid'});
analytics.track('clicked');
mixpanel.track('checkout');
fbq('track', 'Purchase');
fbq('trackCustom', 'CustomThing');
dataLayer.push({event:'checkout_started', value: 'private'});
navigator.sendBeacon('https://private.invalid/events?token=private', 'private-body');
"""
        rows = self.collect(source)["records"]
        self.assertEqual([row["identity"][0] for row in rows],
                         ["gtag_event", "posthog_capture", "analytics_track", "mixpanel_track",
                          "fbq_track", "fbq_track_custom", "data_layer_event", "navigator_send_beacon"])
        self.assertEqual(rows[0]["identity"], ("gtag_event", "purchase"))
        self.assertNotIn("private-value", repr(rows))
        self.assertNotIn("private@example.invalid", repr(rows))
        self.assertNotIn("private-body", repr(rows))

    def test_qualified_globals_and_computed_literal_members_are_supported(self):
        result = self.identities("window.posthog['capture']('a'); globalThis['gtag']('event','b'); window.navigator.sendBeacon('/events');")
        self.assertEqual(result, [("posthog_capture", "a"), ("gtag_event", "b"), ("navigator_send_beacon", "/events")])

    def test_self_and_global_qualified_calls_and_writes_are_consistent(self):
        self.assertEqual(self.identities("self.posthog.capture('a'); global.gtag('event','b');"),
                         [("posthog_capture", "a"), ("gtag_event", "b")])
        for root in ("self", "global"):
            result = self.collect(root + ".posthog=custom;posthog.capture('bad');")
            self.assertEqual(result["records"], [])
            self.assertEqual(result["mutated_roots"], ["posthog"])
            self.assertTrue(result["tracking_identity_uncertain"])

    def test_classic_this_and_window_document_frame_alias_writes_are_uncertain(self):
        prefixes = ["this.analytics={track(){}};", "(function(root){root.analytics={track(){}}})(this);",
                    "document.defaultView.analytics={track(){}};", "const w=document.defaultView;w.analytics={track(){}};",
                    "const w=document['defaultView'];w.analytics={track(){}};",
                    "parent.analytics={track(){}};", "frames.analytics={track(){}};", "top.analytics={track(){}};",
                    "window.top.analytics={track(){}};", "window.parent.analytics={track(){}};",
                    "window.frames.analytics={track(){}};", "window.frames[0].analytics={track(){}};",
                    "window.document.defaultView.analytics={track(){}};"]
        for prefix in prefixes:
            with self.subTest(alias=prefix.split(".")[0]):
                result = self.collect(prefix + "analytics.track('Signup');")
                self.assertEqual(result["records"], [])
                self.assertIn("analytics", result["mutated_roots"])
                self.assertTrue(result["tracking_identity_uncertain"])

    def test_shadowed_document_and_frame_roots_do_not_mutate_real_global_sdk(self):
        for root, path in (("document", "document.defaultView"), ("parent", "parent"), ("frames", "frames")):
            result = self.collect("function local(" + root + "){" + path + ".analytics={track(){}};}analytics.track('good');")
            self.assertEqual([row["identity"] for row in result["records"]], [("analytics_track", "good")])
            self.assertEqual(result["mutated_roots"], [])

    def test_this_positive_hints_require_classic_lexical_global_context(self):
        self.assertEqual(self.identities("this.analytics.track('top');(()=>this.analytics.track('arrow'))();"),
                         [("analytics_track", "top"), ("analytics_track", "arrow")])
        for source, options in (("this.analytics.track('bad');", {"module_mode": True}),
                                ("function f(){this.analytics.track('bad');}", {}),
                                ("const o={f(){this.analytics.track('bad');}};", {}),
                                ("class C{f(){this.analytics.track('bad');}}", {}),
                                ("function gtag(){this.dataLayer.push(arguments);}gtag('event','bad');", {})):
            self.assertEqual(self.identities(source, **options), [])

    def test_unknown_local_this_mutations_are_conservatively_incomplete(self):
        result = self.collect("function f(){this.analytics=custom;}analytics.track('bad');")
        self.assertEqual(result["records"], [])
        self.assertIn("analytics", result["mutated_roots"])
        self.assertTrue(result["tracking_identity_uncertain"])

    def test_local_callback_this_escapes_preserve_tracking_disappearance(self):
        for body in ("$(this).hide();", "handler.call(this);", "handler.bind(this);",
                     "(()=>$(this).hide())();"):
            prefix = "el.addEventListener('click',function(){" + body + "});"
            before, after = self.collect(prefix + "analytics.track('Signup');"), self.collect(prefix)
            self.assertEqual([row["identity"] for row in before["records"]], [("analytics_track", "Signup")])
            self.assertEqual(after["records"], [])
            self.assertEqual(before["mutated_roots"], [])
            self.assertFalse(before["tracking_identity_uncertain"])

    def test_classic_global_and_lexical_arrow_this_still_escape(self):
        for prefix in ("init(this);", "(()=>init(this))();"):
            result = self.collect(prefix + "analytics.track('Signup');")
            self.assertEqual(result["records"], [])
            self.assertIn("analytics", result["mutated_roots"])
            self.assertTrue(result["tracking_identity_uncertain"])

    def test_readonly_builtins_do_not_hide_a_tracking_disappearance(self):
        for prefix in ("Object.keys(window);", "const names=Reflect.ownKeys(self);", "Object.getOwnPropertyNames(globalThis);"):
            before = self.collect(prefix + "gtag('event','purchase');")
            after = self.collect(prefix)
            self.assertEqual([row["identity"] for row in before["records"]], [("gtag_event", "purchase")])
            self.assertEqual(after["records"], [])
            self.assertEqual(before["mutated_roots"], [])
            self.assertFalse(before["tracking_identity_uncertain"])

    def test_opaque_global_calls_and_replaced_readonly_builtins_stay_uncertain(self):
        for prefix in ("$(window).on('resize',handler);", "custom(window);", "Object.values(window);",
                       "const Object={keys:custom};Object.keys(window);", "Object.keys=custom;Object.keys(window);"):
            result = self.collect(prefix + "gtag('event','purchase');")
            self.assertEqual(result["records"], [])
            self.assertEqual(set(result["mutated_roots"]), pr_js_signals.ROOTS)
            self.assertTrue(result["tracking_identity_uncertain"])

    def test_global_and_sdk_aliases_escaping_into_containers_are_uncertain(self):
        for prefix in ("var w=window;w.posthog=custom;", "const p=posthog;p.capture=noop;",
                       "const a={sdk:posthog};", "consume([posthog]);", "function f(){return self;}",
                       "const f=()=>globalThis;", "const a=flag?posthog:other;",
                       "window.__proto__.posthog=custom;", "window.window.posthog=custom;"):
            result = self.collect(prefix + "posthog.capture('bad');")
            self.assertEqual(result["records"], [])
            self.assertIn("posthog", result["mutated_roots"])
            self.assertTrue(result["tracking_identity_uncertain"])

    def test_actual_vendor_loaders_make_disappearance_incomplete_not_false_clean(self):
        for vendor, (loader, call, root) in VENDOR_LOADERS.items():
            with self.subTest(vendor=vendor):
                before, after = self.collect(loader + call), self.collect(loader)
                self.assertIn(root, before["mutated_roots"])
                self.assertIn(root, after["mutated_roots"])
                self.assertTrue(before["tracking_identity_uncertain"])
                self.assertGreater(before["omitted_count"], 0)
                self.assertIn("global_tracking_mutated", before["limitations"])
                self.assertNotIn("purchase", repr(before["records"]))

    def test_opaque_vendor_bootstrap_does_not_hide_independent_declared_import(self):
        loader = VENDOR_LOADERS["meta"][0]
        result = self.collect("import ph from 'posthog-js';" + loader + "ph.capture('purchase');")
        self.assertEqual([row["identity"] for row in result["records"]], [("posthog_capture", "purchase")])
        self.assertFalse(result["records"][0]["ambient"])

    def test_import_alias_and_export_escapes_are_not_verified_sdk_identities(self):
        for tail in ("const p=ph;p.capture=noop;", "consume({sdk:ph});", "export {ph};", "function f(){return ph;}"):
            result = self.collect("import ph from 'posthog-js';" + tail + "ph.capture('bad');")
            self.assertEqual(result["records"], [])
            self.assertTrue(result["tracking_identity_uncertain"])

    def test_comments_strings_and_member_values_are_not_calls(self):
        result = self.identities("// posthog.capture('bad')\nconst s=\"gtag('event','bad')\"; const other=posthog.capture;")
        self.assertEqual(result, [])

    def test_local_roots_and_parameters_are_not_assumed_to_be_sdks(self):
        examples = ["function gtag(){}; gtag('event','bad');",
                    "const posthog={capture(){}}; posthog.capture('bad');",
                    "function f(analytics){analytics.track('bad')}",
                    "function f(window){window.posthog.capture('bad')}",
                    "import posthog from 'unrecognized-package'; posthog.capture('bad');"]
        for source in examples:
            with self.subTest(source_type=source.split()[0]):
                result = self.collect(source)
                self.assertEqual(result["records"], [])
                self.assertIn("tracking_shadowed_or_dynamic_scope", result["limitations"])

    def test_local_shadow_does_not_hide_other_function_ambient_call(self):
        rows = self.identities("function local(posthog){posthog.capture('bad')} function real(){posthog.capture('good')}")
        self.assertEqual(rows, [("posthog_capture", "good")])

    def test_classic_top_level_sdk_declarations_are_reported_for_peer_suppression(self):
        for declaration in ("var", "let", "const"):
            result = self.collect(declaration + " posthog = custom;")
            self.assertEqual(result["mutated_roots"], ["posthog"])
        module = self.collect("export {}; const posthog = custom;")
        self.assertEqual(module["mutated_roots"], [])

    def test_omitted_candidates_are_counted_without_returning_source(self):
        result = self.collect("function f(posthog){ posthog.capture('private'); } analytics[method]('private'); analytics[method]('again');")
        self.assertEqual(result["records"], [])
        self.assertEqual(result["omitted_count"], 3)

    def test_dynamic_scope_marks_globals_uncertain_for_other_selected_scripts(self):
        result = self.collect("eval(dynamicCode);")
        self.assertEqual(set(result["mutated_roots"]), pr_js_signals.ROOTS)

    def test_with_eval_and_function_ambiguity_are_not_trusted(self):
        examples = ["with (object) { posthog.capture('bad'); }",
                    "eval(code); posthog.capture('bad');",
                    "const e=eval; e(code); gtag('event','bad');",
                    "Function(code); analytics.track('bad');"]
        for source in examples:
            with self.subTest(length=len(source)):
                self.assertEqual(self.identities(source), [])

    def test_assignments_and_member_writes_suppress_affected_global(self):
        examples = ["posthog=other; posthog.capture('bad');",
                    "window.posthog=other; posthog.capture('bad');",
                    "posthog.capture=other; posthog.capture('bad');",
                    "delete window.posthog; posthog.capture('bad');",
                    "window[key]=other; posthog.capture('bad');",
                    "configure(posthog); posthog.capture('bad');",
                    "Object.assign(window,{posthog:other}); posthog.capture('bad');"]
        for source in examples:
            with self.subTest(length=len(source)):
                result = self.collect(source)
                self.assertEqual(result["records"], [])
                self.assertIn("posthog", result["mutated_roots"])
                self.assertIn("global_tracking_mutated", result["limitations"])

    def test_mutation_of_one_sdk_does_not_hide_unrelated_sdk(self):
        result = self.collect("posthog.capture=other; analytics.track('good');")
        self.assertEqual([row["identity"] for row in result["records"]], [("analytics_track", "good")])
        self.assertEqual(result["mutated_roots"], ["posthog"])

    def test_classic_var_shadows_window_property_but_module_local_does_not(self):
        self.assertEqual(self.identities("var posthog=other; window.posthog.capture('bad');"), [])
        self.assertEqual(self.identities("export {}; const posthog=other; window.posthog.capture('good');"),
                         [("posthog_capture", "good")])

    def test_non_tracking_gtag_clock_and_data_layer_non_events_are_not_events(self):
        source = "gtag('js',new Date()); dataLayer.push({commerce: true});"
        self.assertEqual(self.identities(source), [])

    def test_configuration_init_and_pageview_calls_are_retained(self):
        self.assertEqual(self.identities("gtag('config','id'); fbq('init','pixel'); analytics.page(); analytics.page('Checkout');"),
                         [("gtag_config", "id"), ("fbq_init", "pixel"), ("analytics_page", None), ("analytics_page", "Checkout")])

    def test_declared_default_namespace_and_named_import_aliases(self):
        source = """
import ph from 'posthog-js';
import * as events from 'posthog-js';
import {capture as record} from 'posthog-js';
import mp from 'mixpanel-browser';
import * as metrics from 'mixpanel-browser';
import {track as log} from 'mixpanel-browser';
ph.capture('a'); events.capture('b'); events.default.capture('c'); record('d');
mp.track('e'); metrics.track('f'); log('g');
"""
        rows = self.collect(source)["records"]
        self.assertEqual([row["identity"] for row in rows],
                         [("posthog_capture", value) for value in "abcd"] + [("mixpanel_track", value) for value in "efg"])
        self.assertTrue(all(not row["ambient"] for row in rows))
        self.assertTrue(all(row["assumptions"] == ["declared_tracking_import"] for row in rows))

    def test_imported_module_shadowing_mutation_and_escape_are_not_trusted(self):
        sources = ["import ph from 'posthog-js'; ph.capture=custom; ph.capture('bad');",
                   "import {capture as record} from 'posthog-js'; record=custom; record('bad');",
                   "import ph from 'posthog-js'; configure(ph); ph.capture('bad');",
                   "import ph from 'posthog-js'; function mutate(){eval(code)} ph.capture('bad');",
                   "import ph from 'posthog-js'; function f(ph){ph.capture('bad')}"]
        for source in sources:
            self.assertEqual(self.collect(source)["records"], [])

    def test_imported_binding_is_not_suppressed_by_unrelated_global_sdk_mutation(self):
        result = self.collect("import ph from 'posthog-js'; window.posthog=custom; ph.capture('good');")
        self.assertEqual([row["identity"] for row in result["records"]], [("posthog_capture", "good")])
        self.assertFalse(result["records"][0]["ambient"])

    def test_exact_classic_gtag_queue_shim_and_idempotent_initialization(self):
        variants = ["window.dataLayer=window.dataLayer||[];", "globalThis.dataLayer=globalThis.dataLayer||[];",
                    "var dataLayer=dataLayer||[];", "var dataLayer=window.dataLayer||[];"]
        for bootstrap in variants:
            source = bootstrap + "function gtag(){dataLayer.push(arguments);} gtag('config','id');gtag('event','purchase');"
            result = self.collect(source)
            self.assertEqual([row["identity"] for row in result["records"]], [("gtag_config", "id"), ("gtag_event", "purchase")])
            self.assertTrue(all(row["assumptions"] == ["static_queue_shim_only"] for row in result["records"]))
            self.assertEqual(result["mutated_roots"], [])

    def test_modified_or_module_local_queue_shim_is_not_recognized(self):
        sources = ["function gtag(){dataLayer.push(arguments); custom();}gtag('event','bad');",
                   "function gtag(a){dataLayer.push(arguments);}gtag('event','bad');",
                   "function gtag(){other.push(arguments);}gtag('event','bad');",
                   "window.dataLayer=[];function gtag(){dataLayer.push(arguments);}gtag('event','bad');",
                   "window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}window.dataLayer=[];gtag('event','bad');",
                   "window.dataLayer=custom||[];function gtag(){dataLayer.push(arguments);}gtag('event','bad');",
                   "function gtag(){dataLayer.push(arguments);}eval(code);gtag('event','bad');"]
        for source in sources:
            self.assertEqual(self.collect(source)["records"], [])
        source = "function gtag(){dataLayer.push(arguments);}gtag('event','bad');"
        self.assertEqual(self.collect(source, module_mode=True)["records"], [])

    def test_endpoint_calls_project_only_private_urls_methods_and_generic_evidence(self):
        source = "fetch('https://private.invalid/api/orders?token=private',{method:'POST',body:JSON.stringify({secret:'private-body'})});"
        result = self.collect(source)
        self.assertEqual(result["endpoint_calls"], [{"url": "https://private.invalid/api/orders?token=private",
                          "method": "POST", "client": "fetch", "evidence": {"source": "source_1", "location": "line 1"},
                          "confidence": "inferred", "assumptions": [], "assumed_global": False, "assumed_bare": False}])
        self.assertNotIn("private-body", repr(result))

    def test_endpoint_projection_uses_the_same_parse_request(self):
        real_parse = pr_js_signals.parse
        with patch.object(pr_js_signals, "parse", wraps=real_parse) as parsed:
            result = self.collect("import axios from 'axios'; axios.post('/api/orders'); posthog.capture('done');")
        self.assertEqual(parsed.call_count, 1)
        self.assertEqual(result["endpoint_calls"][0]["method"], "POST")

    def test_unknown_http_method_and_global_axios_suppression_metadata(self):
        result = self.collect("fetch('/api/orders',{method:dynamicMethod});axios.get('/api/orders');")
        self.assertIsNone(result["endpoint_calls"][0]["method"])
        self.assertTrue(result["endpoint_calls"][1]["assumed_global"])
        self.assertTrue(result["endpoint_calls"][1]["assumed_bare"])
        for prefix in ("window.axios=custom;", "self.axios=custom;", "globalThis.axios.defaults.baseURL=custom;"):
            result = self.collect(prefix + "axios.get('/api/orders');")
            self.assertTrue(result["global_axios_modified"])
            self.assertTrue(result["global_axios_uncertain"])
            self.assertTrue(result["axios_identity_uncertain"])
            self.assertEqual(result["endpoint_calls"], [])
        result = self.collect("const axios=custom;window.axios.get('/api/orders');")
        self.assertTrue(result["global_axios_lexical"])
        self.assertFalse(result["endpoint_calls"][0]["assumed_bare"])

    def test_shared_deadline_is_checked_after_parse_and_during_construction(self):
        document = js_parser.parse("posthog.capture('x');")
        with self.assertRaises(ValueError):
            pr_js_signals._TrackingAnalyzer(document, False, time.monotonic() - 1)
        real_parse = pr_js_signals.parse
        def delayed_parse(*args):
            value = real_parse(*args)
            time.sleep(0.02)
            return value
        with patch.object(pr_js_signals, "parse", side_effect=delayed_parse):
            with self.assertRaises(ValueError) as caught:
                self.collect("posthog.capture('" + CANARY + "');", deadline=time.monotonic() + 0.01)
        self.assertEqual(str(caught.exception), pr_js_signals._ERROR)

    def test_dynamic_event_is_fixed_none_identity_not_argument_source(self):
        result = self.collect("posthog.capture(privateVariable); dataLayer.push({event: privateVariable});")
        self.assertEqual([row["identity"] for row in result["records"]],
                         [("posthog_capture", None), ("data_layer_event", None)])
        self.assertTrue(all(not row["event_literal"] for row in result["records"]))
        self.assertIn("tracking_dynamic_event", result["limitations"])
        self.assertNotIn("privateVariable", repr(result))

    def test_dynamic_callee_and_dynamic_gtag_command_are_explicit_limits(self):
        result = self.collect("posthog[method]('ignored'); gtag(command,'ignored');")
        self.assertEqual(result["records"], [])
        self.assertIn("tracking_dynamic_callee", result["limitations"])
        self.assertIn("tracking_dynamic_command", result["limitations"])

    def test_data_layer_last_event_property_and_unknown_spread_order(self):
        result = self.identities("dataLayer.push({event:'old',event:'new'}); dataLayer.push({event:'x',...unknown}); dataLayer.push({...unknown,event:'y'});")
        self.assertEqual(result, [("data_layer_event", "new"), ("data_layer_event", None), ("data_layer_event", "y")])

    def test_static_event_expressions_can_be_resolved_without_execution(self):
        self.assertEqual(self.identities("const event='sign'+'_up'; posthog.capture(event);"),
                         [("posthog_capture", "sign_up")])

    def test_typescript_jsx_and_optional_calls_are_source_hints(self):
        rows = self.identities("const event: string='signup'; posthog?.capture?.(event); const page=<div/>;", kind="typescript")
        self.assertEqual(rows, [("posthog_capture", "signup")])

    def test_literal_and_api_changes_preserve_distinct_private_identities(self):
        first = self.identities("posthog.capture('" + CANARY + "');")
        second = self.identities("analytics.track('" + CANARY + "');")
        self.assertNotEqual(first, second)
        self.assertEqual(first, [("posthog_capture", CANARY)])

    def test_line_moves_and_call_reordering_do_not_change_identity_multisets(self):
        before = self.identities("posthog.capture('a'); analytics.track('b');")
        after = self.identities("\nanalytics.track('b');\nposthog.capture('a');")
        self.assertEqual(sorted(before), sorted(after))

    def test_duplicates_are_retained_for_occurrence_count_comparison(self):
        self.assertEqual(self.identities("posthog.capture('a'); posthog.capture('a');"),
                         [("posthog_capture", "a"), ("posthog_capture", "a")])

    def test_max_records_and_syntax_errors_fail_closed_without_canaries(self):
        for source, limit in [("posthog.capture('a'); posthog.capture('b');", 1),
                              ("posthog.capture('" + CANARY + "'", 256)]:
            with patch.object(pr_js_signals, "MAX_RECORDS", limit):
                with self.assertRaises(ValueError) as caught:
                    self.collect(source)
            self.assertEqual(str(caught.exception), pr_js_signals._ERROR)
            self.assertNotIn(CANARY, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
