"""Fictional branch profiles; no provider traffic or real-shop claims."""
from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from llm.v2.course import profile_features as features, evidence_memory as evidence, recommendation_policy as policy, agent

PLACE = {"placeId":"123", "name":"가상카페", "address":"서울 송파구 가상로 10", "lat":37.51, "lng":127.08,
         "category":"CAFE", "detail":"음식점 > 카페"}
URL = "https://www.diningcode.com/profile.php?rid=fixture123"


def page(texts=None):
    texts = texts or ["매장이 조용하고 테이블 간격도 넓어서 편안했어요.", "조용한 분위기가 마음에 들고 자리도 넉넉했어요."]
    date = (timezone.localdate()-timedelta(days=3)).isoformat()
    return {"body_read":True,"visible_text_complete":True,"title":"가상카페 - 카페","body_text":PLACE['address']+'\n'+'\n'.join(texts),
            "metadata":{"profile":{"review":[{"datePublished":date,"description":text} for text in texts]}}}


def requested(term="조용함",intent="required"):
    return evidence.Requirement(term=term,attribute="review_feature",intent=intent,group="feature")


def claims(proof, term="조용함", **changes):
    return {"items":[{"place_id":"123","term":term,"review_index":i,"polarity":"positive",
        "quote":r['text'],"general_context":True,"category":"atmosphere",**changes} for i,r in enumerate(proof['reviews'])]}


class ProfileFeatureTests(SimpleTestCase):
    def proof(self, p=None):
        p=p or page()
        return {"place":PLACE,"url":URL,"reviews":features.reviews(p),"page":p}

    def interpret(self, proof, payload=None):
        with patch.object(agent,'llm') as model:
            model.return_value.with_structured_output.return_value.invoke.return_value=payload or claims(proof)
            return features.interpret([proof],[requested()])

    def test_two_visible_recent_experiences_on_same_page_are_independent(self):
        result=self.interpret(self.proof())
        rows=evidence.checked_rows(PLACE,[requested()],result,{URL},timezone.now())
        self.assertEqual(len(rows),2)
        self.assertEqual(len({r['experience_key'] for r in rows}),2)
        self.assertEqual(evidence.verdict(rows,requested()),'match')

    def test_one_subjective_review_passes_but_duplicate_facilities_or_conflicts_do_not(self):
        proof=self.proof()
        proof['reviews']=proof['reviews'][:1]
        rows=evidence.checked_rows(PLACE,[requested()],self.interpret(proof),{URL},timezone.now())
        self.assertEqual(evidence.verdict(rows+rows,requested()),'match')
        facilities=[{**r,'term':'콘센트'} for r in rows]
        self.assertEqual(evidence.verdict(facilities+facilities,requested('콘센트')),'unknown')
        p=page(['카페가 조용하고 편안했어요.']*2)
        self.assertEqual(len(features.reviews(p)),1)
        proof=self.proof(); payload=claims(proof); payload['items'][1]['polarity']='negative'
        rows=evidence.checked_rows(PLACE,[requested()],self.interpret(proof,payload),{URL},timezone.now())
        self.assertEqual(evidence.verdict(rows,requested()),'unknown')

    def test_future_advertised_and_nonvisible_reviews_are_rejected(self):
        for change in ('future','ad','invisible','metadata_only'):
            with self.subTest(change=change):
                p=page(['조용하고 넓은 매장이어서 편안하게 쉬었어요.'])
                review=p['metadata']['profile']['review'][0]
                if change=='future':review['datePublished']=(timezone.localdate()+timedelta(days=1)).isoformat()
                if change=='ad':review['description']+=' 협찬으로 제공받았어요.'; p['body_text']+=review['description']
                if change=='invisible':p['body_text']='다른 가게 추천 카드만 있음'
                if change=='metadata_only':review['description']='실제 후기에 없는 콘센트와 무료주차가 있대요.'
                self.assertEqual(features.reviews(p),[])

    def test_single_older_review_only_suffices_for_requested_subjective_traits(self):
        p=page(['매장이 조용하고 넓고 분위기가 좋아서 쉬기 좋았어요.'])
        p['metadata']['profile']['review'][0]['datePublished']=(timezone.localdate()-timedelta(days=200)).isoformat()
        proof=self.proof(p)
        self.assertEqual(len(proof['reviews']),1)
        for term in ('조용함','조용한','넓은 매장','넓음','분위기 좋음','좋은 분위기'):
            req=requested(term)
            with patch.object(agent,'llm') as model:
                model.return_value.with_structured_output.return_value.invoke.return_value=claims(proof,term)
                findings=features.interpret([proof],[req])
            rows=evidence.checked_rows(PLACE,[req],findings,{URL},timezone.now())
            self.assertEqual(evidence.verdict(rows,req),'match')
            exclude=requested(term,'exclude')
            self.assertEqual(evidence.verdict(evidence.checked_rows(PLACE,[exclude],findings,{URL},timezone.now()),exclude),'unknown')
            for facility in ('콘센트','주차 가능'):
                f=findings[0].model_copy(update={'term':facility})
                facility_req=requested(facility)
                facility_rows=evidence.checked_rows(PLACE,[facility_req],[f],{URL},timezone.now())
                self.assertEqual(len(facility_rows),1)
                self.assertEqual(evidence.verdict(facility_rows,facility_req),'unknown')

    def test_rich_menu_markup_does_not_discard_visible_review_but_quote_must_be_visible(self):
        p=page(['매장이 넓고 분위기가 좋아서 편안했어요. 커피를 마셨어요.'])
        p['metadata']['profile']['review'][0]['description']='매장이 넓고 분위기가 좋아서 편안했어요. <m-메뉴>커피</m-메뉴>를 마셨어요.'
        proof=self.proof(p)
        self.assertEqual(len(proof['reviews']),1)
        payload=claims(proof,quote='매장이 넓고 분위기가 좋아서 편안했어요.')
        self.assertEqual(len(self.interpret(proof,payload)),1)
        p['body_text']=PLACE['address']+' 커피를 마셨어요.'
        self.assertEqual(self.interpret(proof,payload),[])

    def test_nearby_address_diversity_does_not_promote_remote_cached_places(self):
        near={**PLACE,'placeId':'near','address':'가상로 1','lat':37.5101}
        colocated={**near,'placeId':'colocated'}
        next_address={**PLACE,'placeId':'next','address':'가상로 2','lat':37.511}
        remote={**PLACE,'placeId':'remote','address':'가상로 3','lat':37.54}
        with patch.object(features.place_quality,'resolve',side_effect=lambda p: {'reviewUrl':URL} if p['placeId']=='remote' else None):
            order=features.investigation_order([remote,near,colocated,next_address],PLACE)
        self.assertEqual([p['placeId'] for p in order],['near','next','colocated','remote'])

    @override_settings(COURSE_RECOMMENDATION_MODE='balanced')
    def test_confirmed_feature_keeps_source_without_unrelated_quality_notice(self):
        p={**PLACE,'conditionChecks':[{'attribute':'review_feature','term':'조용함','status':'match','intent':'required'}],
           'verifiedFacts':[{'attribute':'review_feature','term':'조용함','url':URL}]}
        answer='카페 방문 '+evidence.citation_links(p)
        with policy.request('조용한 카페'), patch.object(features.place_quality,'active',return_value=True), \
                patch.object(features.place_quality,'resolve',return_value=None):
            result=policy.explain({'places':[p],'answer':answer})
        self.assertIn(URL,result['answer'])
        self.assertNotIn(policy.NOTICE,result['answer'])

    def test_branch_identity_and_complete_body_are_required(self):
        for change in ({'title':'다른카페'}, {'body_text':'서울 송파구 가상로 100'}, {'visible_text_complete':False}, {'body_read':False}):
            reader=Mock(read=Mock(return_value={**page(),**change}))
            with patch.object(features.place_quality,'resolve',return_value={'reviewUrl':URL}):
                self.assertIsNone(features.read_profile(PLACE,reader))

    def test_quote_wrong_place_unknown_term_and_scoped_claims_are_rejected(self):
        proof=self.proof()
        for change in ({'quote':'카페가 매일 무료주차를 제공한다'}, {'place_id':'999'}, {'term':'무료주차'},
                       {'review_index':99}, {'general_context':False}):
            with self.subTest(change=change):
                self.assertEqual(self.interpret(proof,claims(proof,**change)),[])

    @override_settings(COURSE_WEB_VERIFICATION_ENABLED=False)
    def test_empty_db_and_disabled_web_search_still_use_known_profiles_once(self):
        proof=self.proof()
        findings=self.interpret(proof)
        reader=Mock()
        with evidence.request_budget(), patch.object(evidence,'requirements',return_value=[requested()]), \
                patch.object(evidence,'read',return_value=[]), patch.object(features,'read_profile',return_value=proof) as read, \
                patch.object(features,'interpret',return_value=findings) as interpret, \
                patch.object(features.quality_research,'ProfileReader',return_value=reader), \
                patch.object(features,'PublicReader'), patch.object(evidence,'_search_once') as search:
            self.assertEqual(len(evidence.enrich([PLACE],['조용한'])),1)
            self.assertEqual(len(evidence.enrich([dict(PLACE)],['조용한'])),1)
            read.assert_called_once(); interpret.assert_called_once(); search.assert_not_called()
        reader.close.assert_called_once()

    def test_failed_profiles_are_bounded_and_never_retried_across_corridor_passes(self):
        candidates=[{**PLACE,'placeId':str(i+1)} for i in range(30)]
        budget={}
        with patch.object(features,'read_profile',return_value=None) as read, \
                patch.object(features.quality_research,'ProfileReader'), patch.object(features,'PublicReader'), \
                patch.object(features,'interpret') as interpret:
            self.assertEqual(features.collect(candidates,[requested()],budget),([],set()))
            self.assertEqual(features.collect(candidates,[requested()],budget),([],set()))
            self.assertEqual(features.collect(candidates,[requested()],budget),([],set()))
            self.assertEqual(features.collect(candidates,[requested()],budget),([],set()))
            self.assertEqual(read.call_count,features.MAX_CANDIDATES)
            interpret.assert_not_called()

    def test_dense_origin_pool_preserves_reads_for_new_route_candidates(self):
        nearby=[{**PLACE,'placeId':str(i)} for i in range(40)]
        next_pool=[{**PLACE,'placeId':'new-'+str(i)} for i in range(3)]
        budget={}
        with patch.object(features,'read_profile',return_value=None) as read, \
                patch.object(features.quality_research,'ProfileReader'), patch.object(features,'PublicReader'):
            features.collect(nearby,[requested()],budget)
            self.assertEqual(read.call_count,features.MAX_PER_PASS)
            read.reset_mock()
            features.collect(nearby+next_pool,[requested()],budget)
        self.assertEqual({call.args[0]['placeId'] for call in read.call_args_list[:3]}, {'new-0','new-1','new-2'})

    def test_cancelled_profile_read_propagates_and_closes_reader(self):
        from llm.service.chat_runs import Stopped
        reader=Mock()
        with patch.object(features,'read_profile',side_effect=Stopped()), \
                patch.object(features.quality_research,'ProfileReader',return_value=reader), patch.object(features,'PublicReader'):
            with self.assertRaises(Stopped), evidence.request_budget():
                features.collect([PLACE],[requested()],evidence._BUDGET.get())
        reader.close.assert_called_once()
        self.assertIsNone(evidence._BUDGET.get())

    @override_settings(COURSE_RECOMMENDATION_MODE='balanced')
    def test_features_skip_unrelated_interior_audit_but_still_require_evidence(self):
        for question in ('조용한 카페','넓은 카페','분위기 좋은 카페','콘센트 있는 카페','주차 가능한 카페'):
            with policy.request(question):self.assertTrue(policy.balanced())
        with policy.request('깔끔한 실내를 후기에서 확인해줘'):self.assertFalse(policy.balanced())
        self.assertFalse(evidence.condition_result([], [requested()], set())[1])
