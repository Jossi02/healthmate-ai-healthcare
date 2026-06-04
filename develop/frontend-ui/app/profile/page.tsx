"use client";

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import Image from 'next/image';
import { Edit3, Target, Bell, LogOut, ChevronRight, CheckCircle2, HeadphonesIcon, Info, X, User, Check, Loader2, MessageCircle } from 'lucide-react';
import { motion } from 'framer-motion';
import {
  AUTH_TOKEN_STORAGE_KEY,
  clearClientAuthState,
  redirectToLoginForExpiredSession,
} from '@/lib/auth';
import {
  AI_PERSONAS,
  type AiPersonaId,
  resolveVisiblePersona,
} from '@/lib/personas';
import { usePlan, UserData } from '../context/PlanContext';

type EditProfileForm = Partial<UserData> & { otherAllergy?: string };
type ProfileNotice = {
  title: string;
  message: string;
} | null;

function getApiBaseUrl() {
  const raw =
    process.env.NEXT_PUBLIC_BACKEND_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    '';
  return raw.endsWith('/') ? raw.slice(0, -1) : raw;
}

function buildApiUrl(path: string) {
  const baseUrl = getApiBaseUrl();
  return baseUrl ? `${baseUrl}${path}` : path;
}

export default function ProfilePage() {
  const router = useRouter();
  const { userData, updateUserData } = usePlan();
  const selectedPersona = resolveVisiblePersona(userData?.selected_ai_persona);
  
  // Modals state
  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const [isGoalModalOpen, setIsGoalModalOpen] = useState(false);
  const [editForm, setEditForm] = useState<EditProfileForm>({});
  const [editGoal, setEditGoal] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [isPersonaSaving, setIsPersonaSaving] = useState(false);
  const [personaError, setPersonaError] = useState('');
  const [profileError, setProfileError] = useState('');
  const [profileNotice, setProfileNotice] = useState<ProfileNotice>(null);

  const handleLogout = () => {
    clearClientAuthState();
    router.push('/login');
  };

  const handleOpenEdit = () => {
    setEditForm({
      ...userData,
      conditions: userData?.conditions || [],
      allergies: userData?.allergies || [],
      otherAllergy: userData?.otherAllergy || '',
    });
    setIsEditModalOpen(true);
  };

  const handleOpenGoal = () => {
    setEditGoal(userData?.goal || '');
    setIsGoalModalOpen(true);
  };

  const saveProfileToAPI = async (data: EditProfileForm) => {
    setIsSaving(true);
    setProfileError('');
    const finalData = { ...data };
    let shouldApplyLocalUpdate = false;

    try {
      const payload = {
        user_id: data.user_id || data.email || 'unknown',
        nickname: data.nickname || '',
        mbti: data.mbti || '',
        gender: data.gender || 'male',
        age: Number(data.age) || 0,
        height: Number(data.height) || 0,
        weight: Number(data.weight) || 0,
        bmi: 0, // BMI 계산은 백엔드에서 수행
        goal: data.goal || '',
        activity_level: data.activityLevel || '',
        medical_history: data.conditions || [],
        allergies: data.allergies || [],
      };

      const res = await fetch(buildApiUrl('/api/v1/users/profile'), {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'ngrok-skip-browser-warning': 'true',
          'Authorization': `Bearer ${localStorage.getItem(AUTH_TOKEN_STORAGE_KEY)}`
        },
        body: JSON.stringify(payload)
      });

      if (res.status === 401) {
        redirectToLoginForExpiredSession();
        return;
      }
      
      if (!res.ok) {
        throw new Error('Failed to save profile.');
      }

      const responseData = await res.json();
      // 백엔드 응답 형태에 맞게 분기 (data 속성 내부 또는 최상단)
      if (responseData?.data?.bmi) {
        finalData.bmi = responseData.data.bmi;
      } else if (responseData?.bmi) {
        finalData.bmi = responseData.bmi;
      }

      shouldApplyLocalUpdate = true;
    } catch (error) {
      console.error('API Error:', error);
      setProfileError('프로필 저장에 실패했어요. 잠시 뒤 다시 시도해주세요.');
    } finally {
      if (shouldApplyLocalUpdate) {
        updateUserData(finalData);
        setIsEditModalOpen(false);
        setIsGoalModalOpen(false);
      }
      setIsSaving(false);
    }
  };

  const handleFormChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const { name, value } = e.target;
    setEditForm((prev: EditProfileForm) => ({ ...prev, [name]: value }));
  };

  const conditionOptions = ['고혈압', '당뇨', '관절염', '천식', '심혈관 질환', '없음'];
  const allergyOptions = ['유제품', '견과류', '갑각류', '밀', '대두', '달걀', '해당 없음'];

  const handleCheckboxChange = (condition: string) => {
    setEditForm((prev: EditProfileForm) => {
      let newConditions: string[] = [...(prev.conditions || [])];
      if (condition === '없음') {
        newConditions = newConditions.includes('없음') ? [] : ['없음'];
      } else {
        newConditions = newConditions.filter((c: string) => c !== '없음');
        if (newConditions.includes(condition)) {
          newConditions = newConditions.filter((c: string) => c !== condition);
        } else {
          newConditions.push(condition);
        }
      }
      return { ...prev, conditions: newConditions };
    });
  };

  const handleAllergyChange = (allergy: string) => {
    setEditForm((prev: EditProfileForm) => {
      let newAllergies: string[] = [...(prev.allergies || [])];
      if (allergy === '해당 없음') {
        newAllergies = newAllergies.includes('해당 없음') ? [] : ['해당 없음'];
        return { ...prev, allergies: newAllergies, otherAllergy: '' };
      } else {
        newAllergies = newAllergies.filter((a: string) => a !== '해당 없음');
        if (newAllergies.includes(allergy)) {
          newAllergies = newAllergies.filter((a: string) => a !== allergy);
        } else {
          newAllergies.push(allergy);
        }
      }
      return { ...prev, allergies: newAllergies };
    });
  };

  const handlePersonaSelect = async (personaId: AiPersonaId) => {
    if (personaId === selectedPersona.id || isPersonaSaving) return;

    const token = localStorage.getItem(AUTH_TOKEN_STORAGE_KEY);
    if (!token) {
      redirectToLoginForExpiredSession();
      return;
    }

    const userId = userData?.user_id || userData?.login_id || userData?.email;
    if (!userId) {
      setPersonaError('프로필을 불러온 뒤 다시 선택해주세요.');
      return;
    }

    const previousPersona = userData?.selected_ai_persona || selectedPersona.id;
    setIsPersonaSaving(true);
    setPersonaError('');
    updateUserData({ selected_ai_persona: personaId });

    try {
      const response = await fetch(
        buildApiUrl(
          `/api/v1/users/${encodeURIComponent(userId)}/settings/persona`
        ),
        {
          method: 'PATCH',
          headers: {
            'Content-Type': 'application/json',
            'ngrok-skip-browser-warning': 'true',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            selected_ai_persona: personaId,
          }),
        }
      );

      if (response.status === 401) {
        redirectToLoginForExpiredSession();
        return;
      }

      if (!response.ok) {
        throw new Error('Persona API request failed.');
      }
    } catch (error) {
      console.error('Persona save error:', error);
      updateUserData({ selected_ai_persona: previousPersona });
      setPersonaError('AI 코치 설정 저장에 실패했어요. 다시 시도해주세요.');
    } finally {
      setIsPersonaSaving(false);
    }
  };

  const menuItems = [
    { title: '내 정보 수정', icon: Edit3, color: 'text-blue-500', bg: 'bg-blue-50', onClick: handleOpenEdit },
    { title: '운동 목표 설정', icon: Target, color: 'text-purple-500', bg: 'bg-purple-50', onClick: handleOpenGoal },
    {
      title: '알림 설정',
      icon: Bell,
      color: 'text-orange-500',
      bg: 'bg-orange-50',
      onClick: () =>
        setProfileNotice({
          title: '알림 설정',
          message: '데모 버전에서는 알림 채널 연결 전이에요. 플랜 변경 표시는 하단 건강 추천 탭 배지로 확인할 수 있어요.',
        }),
    },
    {
      title: '고객 문의',
      icon: HeadphonesIcon,
      color: 'text-green-500',
      bg: 'bg-green-50',
      onClick: () =>
        setProfileNotice({
          title: '고객 문의',
          message: '데모 문의는 팀 운영 채널로 전달해주세요. 정식 문의 폼은 다음 버전에서 연결할 예정이에요.',
        }),
    },
    { title: '현재 버전 1.0.0', icon: Info, color: 'text-gray-500', bg: 'bg-gray-50', onClick: () => {}, disabled: true },
  ];

  return (
    <div className="min-h-screen h-screen overflow-y-auto bg-[#f8fafc] text-gray-900 font-sans p-6 pb-28 md:p-8 lg:p-12">
      <div className="max-w-4xl mx-auto space-y-8 pt-4 pb-12">
        
        {/* Profile Header */}
        <h1 className="text-3xl font-bold text-gray-900 tracking-tight pl-2">내 정보</h1>

        {/* Profile Summary Card */}
        <div className="bg-white rounded-3xl p-6 shadow-[0_8px_30px_-6px_rgba(0,0,0,0.08)] border border-gray-100 flex items-center space-x-5 relative overflow-hidden group">
          <div className="absolute top-0 right-0 w-32 h-32 bg-blue-50 rounded-full blur-3xl -mr-10 -mt-10 opacity-60"></div>
          
          <div className="w-20 h-20 bg-gradient-to-br from-[#2563eb] to-indigo-600 rounded-2xl flex items-center justify-center text-white shadow-[0_8px_16px_-4px_rgba(37,99,235,0.4)] relative z-10 shrink-0 transform group-hover:scale-105 transition-transform duration-300">
            <User className="w-10 h-10" strokeWidth={1.5} />
          </div>
          
          <div className="flex-1 relative z-10 w-full">
            <div className="flex items-center space-x-2 mb-1.5">
              <h2 className="text-xl font-bold text-gray-900">{userData ? (userData.nickname || userData.name) : '사용자'}</h2>
              <CheckCircle2 className="w-4 h-4 text-[#2563eb]" />
            </div>
            
            {userData ? (
              <p className="text-[13px] md:text-sm font-semibold text-gray-600 mb-3.5 tracking-tight flex items-center flex-wrap gap-y-1">
                만 {userData.age}세 <span className="text-gray-300 mx-1.5">|</span> 
                {userData.gender} <span className="text-gray-300 mx-1.5">|</span> 
                {userData.height}cm <span className="text-gray-300 mx-1.5">|</span> 
                {userData.weight}kg
              </p>
            ) : (
              <p className="text-sm text-gray-500 font-medium mb-3">user@example.com</p>
            )}
            
            <div className="flex flex-wrap gap-2">
              <div className="inline-flex items-center bg-blue-50 px-2.5 py-1 md:px-3 md:py-1.5 rounded-full border border-blue-100 shadow-sm">
                <span className="text-[10px] md:text-[11px] font-bold text-[#2563eb] tracking-wide">
                  평소 활동량: {userData ? userData.activityLevel : '보통'}
                </span>
              </div>
              <div className="inline-flex items-center bg-purple-50 px-2.5 py-1 md:px-3 md:py-1.5 rounded-full border border-purple-100 shadow-sm">
                <span className="text-[10px] md:text-[11px] font-bold text-purple-600 tracking-wide">
                  목표: {userData ? userData.goal : '건강 유지'}
                </span>
              </div>
              <div className="inline-flex items-center bg-orange-50 px-2.5 py-1 md:px-3 md:py-1.5 rounded-full border border-orange-100 shadow-sm mt-1 md:mt-0">
                <span className="text-[10px] md:text-[11px] font-bold text-orange-600 tracking-wide whitespace-normal">
                  알레르기: {userData ? (
                    (Array.isArray(userData.allergies) ? userData.allergies : []).includes('해당 없음') || 
                    ((Array.isArray(userData.allergies) ? userData.allergies : []).length === 0 && !userData.otherAllergy)
                      ? '없음'
                      : [
                          ...(Array.isArray(userData.allergies) ? userData.allergies : []).filter((a: string) => a !== '기타(직접 입력)' && a !== '해당 없음'),
                          ...((Array.isArray(userData.allergies) ? userData.allergies : []).includes('기타(직접 입력)') && userData.otherAllergy ? [userData.otherAllergy] : [])
                        ].join(', ')
                  ) : '없음'}
                </span>
              </div>
              <div className="inline-flex items-center bg-[#facc15]/15 px-2.5 py-1 md:px-3 md:py-1.5 rounded-full border border-[#facc15]/40 shadow-sm mt-1 md:mt-0">
                <span className="text-[10px] md:text-[11px] font-bold text-[#ca8a04] tracking-wide whitespace-normal">
                  MBTI: {userData?.mbti || '미설정'}
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* AI Persona Section */}
        <section className="overflow-hidden rounded-3xl border border-gray-100 bg-white shadow-[0_8px_30px_-6px_rgba(0,0,0,0.08)]">
          <div className={`relative bg-gradient-to-br ${selectedPersona.accent} px-6 py-7 text-white md:px-8`}>
            <div className="absolute inset-0 bg-black/5" />
            <div className="relative z-10 flex flex-col gap-6 md:flex-row md:items-center">
              <div className="mx-auto flex h-36 w-36 shrink-0 items-center justify-center overflow-hidden rounded-[2rem] bg-white/20 shadow-2xl ring-1 ring-white/30 md:mx-0 md:h-40 md:w-40">
                <Image
                  src={selectedPersona.imageSrc}
                  alt={selectedPersona.imageAlt}
                  width={160}
                  height={160}
                  priority
                  unoptimized
                  className="h-full w-full object-cover"
                />
              </div>
              <div className="min-w-0 text-center md:text-left">
                <div className="mb-3 inline-flex items-center gap-2 rounded-full bg-white/20 px-3 py-1 text-xs font-bold ring-1 ring-white/30">
                  <MessageCircle className="h-3.5 w-3.5" />
                  현재 나와 함께하는 AI 코치
                </div>
                <h2 className="text-3xl font-black tracking-tight md:text-4xl">
                  {selectedPersona.name}
                </h2>
                <p className="mt-3 max-w-xl text-sm font-semibold leading-relaxed text-white/90 md:text-base">
                  “{selectedPersona.intro}”
                </p>
                <p className="mt-3 text-xs font-semibold text-white/75 md:text-sm">
                  {selectedPersona.profileLine}
                </p>
              </div>
            </div>
          </div>

          <div className="px-5 py-5 md:px-6 md:py-6">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <h3 className="text-lg font-black text-gray-900">
                  AI 코치 바꾸기
                </h3>
                <p className="mt-1 text-sm font-medium text-gray-500">
                  내 취향에 맞는 대화 스타일을 선택해보세요.
                </p>
              </div>
              {isPersonaSaving && (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 px-3 py-1.5 text-xs font-bold text-blue-600">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  저장 중
                </span>
              )}
            </div>

            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {AI_PERSONAS.map((persona) => {
                const isSelected = persona.id === selectedPersona.id;

                return (
                  <button
                    key={persona.id}
                    type="button"
                    onClick={() => handlePersonaSelect(persona.id)}
                    disabled={isPersonaSaving}
                    aria-pressed={isSelected}
                    className={`group flex min-h-[132px] items-center gap-4 rounded-2xl border p-4 text-left transition-all disabled:cursor-not-allowed disabled:opacity-70 ${
                      isSelected
                        ? persona.selectedClass
                        : 'border-gray-200 bg-white text-gray-700 hover:border-gray-300 hover:bg-gray-50'
                    }`}
                  >
                    <span
                      className={`flex h-20 w-20 shrink-0 items-center justify-center overflow-hidden rounded-2xl bg-gradient-to-br ${persona.accent} shadow-sm transition-transform group-hover:scale-[1.03]`}
                    >
                      <Image
                        src={persona.imageSrc}
                        alt={persona.imageAlt}
                        width={80}
                        height={80}
                        unoptimized
                        className="h-full w-full object-cover"
                      />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-2 text-base font-black">
                        {persona.name}
                        {isSelected && <Check className="h-4 w-4" />}
                      </span>
                      <span className="mt-1 block text-xs font-semibold leading-relaxed opacity-75">
                        {persona.description}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>

            {personaError && (
              <p className="mt-3 text-sm font-bold text-rose-500">
                {personaError}
              </p>
            )}
          </div>
        </section>

        {/* Menu List */}
        <div className="bg-white rounded-3xl shadow-[0_4px_20px_-6px_rgba(0,0,0,0.06)] border border-gray-100 overflow-hidden">
          {menuItems.map((item, idx) => (
            <button 
              key={idx} 
              onClick={item.onClick}
              disabled={item.disabled}
              className={`w-full flex items-center p-5 transition-colors ${item.disabled ? 'opacity-60 cursor-not-allowed bg-gray-50' : 'hover:bg-gray-50'} ${idx !== menuItems.length - 1 ? 'border-b border-gray-100' : ''}`}
            >
              <div className={`w-10 h-10 rounded-xl ${item.bg} ${item.color} flex items-center justify-center mr-4`}>
                <item.icon className="w-5 h-5" />
              </div>
              <span className="font-bold text-gray-800 text-[16px] flex-1 text-left">{item.title}</span>
              {!item.disabled && <ChevronRight className="w-5 h-5 text-gray-400" />}
            </button>
          ))}
        </div>

        {/* Logout Button */}
        <div className="pt-2">
          <button 
            onClick={handleLogout}
            className="w-full flex items-center justify-center p-5 bg-white rounded-2xl shadow-[0_4px_16px_-6px_rgba(0,0,0,0.05)] border border-gray-100 text-red-500 hover:bg-red-50 hover:text-red-600 transition-all font-bold group"
          >
            <LogOut className="w-5 h-5 mr-2 group-hover:-translate-x-1 transition-transform" />
            로그아웃
          </button>
        </div>

      </div>

      {/* Edit Profile Modal */}
      {isEditModalOpen && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-in fade-in duration-200">
          <div className="bg-white rounded-3xl w-full max-w-md shadow-2xl overflow-hidden flex flex-col max-h-[90vh]">
            <div className="flex justify-between items-center p-5 border-b border-gray-100 bg-gray-50/50">
              <h3 className="text-xl font-bold text-gray-900 tracking-tight">내 정보 수정</h3>
              <button aria-label="내 정보 수정 닫기" onClick={() => setIsEditModalOpen(false)} className="p-2 bg-white rounded-full text-gray-400 hover:text-gray-600 shadow-sm border border-gray-100 transition-colors">
                <X className="w-5 h-5" />
              </button>
            </div>
            
            <div className="p-6 overflow-y-auto flex-1 space-y-5 custom-scrollbar max-h-[70vh]">
              <div className="space-y-4 pb-24">
                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-1.5">닉네임</label>
                  <input type="text" name="nickname" value={editForm.nickname || editForm.name || ''} onChange={handleFormChange} className="w-full p-3.5 bg-gray-50 border border-gray-200 rounded-xl text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all" />
                </div>
                
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-semibold text-gray-700 mb-1.5">나이</label>
                    <input type="number" name="age" value={editForm.age || ''} onChange={handleFormChange} className="w-full p-3.5 bg-gray-50 border border-gray-200 rounded-xl text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all" />
                  </div>
                  <div>
                    <label className="block text-sm font-semibold text-gray-700 mb-1.5">성별</label>
                    <select name="gender" value={editForm.gender || 'male'} onChange={handleFormChange} className="w-full p-3.5 bg-gray-50 border border-gray-200 rounded-xl text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all appearance-none">
                      <option value="male">남성</option>
                      <option value="female">여성</option>
                    </select>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-semibold text-gray-700 mb-1.5">키 (cm)</label>
                    <input type="number" name="height" value={editForm.height || ''} onChange={handleFormChange} className="w-full p-3.5 bg-gray-50 border border-gray-200 rounded-xl text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all" />
                  </div>
                  <div>
                    <label className="block text-sm font-semibold text-gray-700 mb-1.5">몸무게 (kg)</label>
                    <input type="number" name="weight" value={editForm.weight || ''} onChange={handleFormChange} className="w-full p-3.5 bg-gray-50 border border-gray-200 rounded-xl text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all" />
                  </div>
                </div>

                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-1.5">MBTI</label>
                  <select name="mbti" value={editForm.mbti || ''} onChange={handleFormChange} className="w-full p-3.5 bg-gray-50 border border-gray-200 rounded-xl text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all appearance-none">
                    <option value="" disabled>선택해주세요</option>
                    <option value="INFP">INFP</option>
                    <option value="INFJ">INFJ</option>
                    <option value="INTP">INTP</option>
                    <option value="INTJ">INTJ</option>
                    <option value="ISFP">ISFP</option>
                    <option value="ISFJ">ISFJ</option>
                    <option value="ISTP">ISTP</option>
                    <option value="ISTJ">ISTJ</option>
                    <option value="ENFP">ENFP</option>
                    <option value="ENFJ">ENFJ</option>
                    <option value="ENTP">ENTP</option>
                    <option value="ENTJ">ENTJ</option>
                    <option value="ESFP">ESFP</option>
                    <option value="ESFJ">ESFJ</option>
                    <option value="ESTP">ESTP</option>
                    <option value="ESTJ">ESTJ</option>
                  </select>
                </div>
                
                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-1.5">평소 활동량</label>
                  <select name="activityLevel" value={editForm.activityLevel || '보통'} onChange={handleFormChange} className="w-full p-3.5 bg-gray-50 border border-gray-200 rounded-xl text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all appearance-none">
                    <option value="거의 없음">거의 없음 (주로 앉아서 생활)</option>
                    <option value="가벼운 활동">가벼운 활동 (주 1~3회 가벼운 운동)</option>
                    <option value="보통">보통 (주 3~5회 적당한 운동)</option>
                    <option value="격렬한 활동">격렬한 활동 (매일 심한 운동/스포츠)</option>
                  </select>
                </div>

                {/* Underlying Conditions */}
                <div className="space-y-2.5 pt-1">
                  <label className="text-sm font-bold text-gray-700 mb-1.5 block">기저 질환 <span className="text-gray-400 text-xs font-normal ml-1">(다중 선택 가능)</span></label>
                  <div className="flex flex-wrap gap-2">
                    {conditionOptions.map(condition => (
                      <button
                        type="button"
                        key={condition}
                        onClick={() => handleCheckboxChange(condition)}
                        className={`px-4 py-2 rounded-xl text-sm font-medium transition-all ${editForm.conditions?.includes(condition)
                          ? 'bg-[#2563eb] text-white shadow-md shadow-blue-500/20 border-transparent'
                          : 'bg-white text-gray-600 border border-gray-200 hover:border-blue-300 hover:bg-blue-50'
                          }`}
                      >
                        {condition}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Allergies */}
                <div className="space-y-2.5 pt-1">
                  <label className="text-sm font-bold text-gray-700 mb-1.5 block">식품 알레르기 및 주의사항 <span className="text-gray-400 text-xs font-normal ml-1">(다중 선택 가능)</span></label>
                  <div className="flex flex-wrap gap-2">
                    {allergyOptions.map(allergy => (
                      <button
                        type="button"
                        key={allergy}
                        onClick={() => handleAllergyChange(allergy)}
                        className={`px-4 py-2 rounded-full text-sm font-medium transition-all ${editForm.allergies?.includes(allergy)
                          ? 'bg-orange-500 text-white shadow-md shadow-orange-500/20 border-transparent'
                          : 'bg-white text-gray-600 border border-gray-200 hover:border-orange-300 hover:bg-orange-50'
                          }`}
                      >
                        {allergy}
                      </button>
                    ))}
                    <button
                      type="button"
                      onClick={() => handleAllergyChange('기타(직접 입력)')}
                      className={`px-4 py-2 rounded-full text-sm font-medium transition-all ${editForm.allergies?.includes('기타(직접 입력)')
                        ? 'bg-orange-500 text-white shadow-md shadow-orange-500/20 border-transparent'
                        : 'bg-white text-gray-600 border border-gray-200 hover:border-orange-300 hover:bg-orange-50'
                        }`}
                    >
                      기타(직접 입력)
                    </button>
                  </div>

                  {/* Conditional input for 'Other' allergy */}
                  {editForm.allergies?.includes('기타(직접 입력)') && (
                    <motion.div
                      initial={{ opacity: 0, height: 0, marginTop: 0 }}
                      animate={{ opacity: 1, height: 'auto', marginTop: 12 }}
                      className="overflow-hidden"
                    >
                      <input
                        type="text"
                        name="otherAllergy"
                        value={editForm.otherAllergy || ''}
                        onChange={handleFormChange}
                        className="w-full px-5 py-3 rounded-xl bg-orange-50/50 border border-orange-200 focus:outline-none focus:ring-2 focus:ring-orange-500/30 focus:bg-white focus:border-orange-400 transition-all text-sm font-medium placeholder-orange-300 text-gray-900"
                        placeholder="알레르기 정보를 직접 입력해주세요 (예: 복숭아)"
                      />
                    </motion.div>
                  )}
                </div>

              </div>
            </div>

            <div className="p-5 border-t border-gray-100 bg-white">
              {profileError && (
                <p className="mb-3 rounded-xl bg-rose-50 px-4 py-3 text-sm font-bold text-rose-500">
                  {profileError}
                </p>
              )}
              <button 
                onClick={() => saveProfileToAPI(editForm)} 
                disabled={isSaving}
                className="w-full py-4 bg-blue-600 hover:bg-blue-700 text-white font-bold rounded-xl transition-colors shadow-[0_4px_12px_-4px_rgba(37,99,235,0.4)] disabled:opacity-70 flex justify-center items-center"
              >
                {isSaving ? '저장 중...' : '저장하기'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Edit Goal Modal */}
      {isGoalModalOpen && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-in fade-in duration-200">
          <div className="bg-white rounded-3xl w-full max-w-sm shadow-2xl overflow-hidden">
            <div className="flex justify-between items-center p-5 border-b border-gray-100 bg-gray-50/50">
              <h3 className="text-xl font-bold text-gray-900 tracking-tight">운동 목표 설정</h3>
              <button aria-label="운동 목표 설정 닫기" onClick={() => setIsGoalModalOpen(false)} className="p-2 bg-white rounded-full text-gray-400 hover:text-gray-600 shadow-sm border border-gray-100 transition-colors">
                <X className="w-5 h-5" />
              </button>
            </div>
            
            <div className="p-6">
              <label className="block text-sm font-semibold text-gray-700 mb-2">현재의 건강/운동 목표를 선택해주세요</label>
              <select 
                value={editGoal} 
                onChange={(e) => setEditGoal(e.target.value)} 
                className="w-full p-4 bg-gray-50 border border-gray-200 rounded-xl text-gray-900 focus:outline-none focus:ring-2 focus:ring-purple-500/20 focus:border-purple-500 transition-all appearance-none"
              >
                <option value="다이어트">🔥 다이어트</option>
                <option value="근력 향상">💪 근력 향상</option>
                <option value="건강 유지">✨ 건강 유지</option>
              </select>
            </div>

            <div className="p-5 border-t border-gray-100 bg-white">
              {profileError && (
                <p className="mb-3 rounded-xl bg-rose-50 px-4 py-3 text-sm font-bold text-rose-500">
                  {profileError}
                </p>
              )}
              <button 
                onClick={() => saveProfileToAPI({ ...userData, goal: editGoal })} 
                disabled={isSaving || !editGoal.trim()}
                className="w-full py-4 bg-purple-600 hover:bg-purple-700 text-white font-bold rounded-xl transition-colors shadow-[0_4px_12px_-4px_rgba(147,51,234,0.4)] disabled:opacity-70 flex justify-center items-center"
              >
                {isSaving ? '저장 중...' : '목표 저장하기'}
              </button>
            </div>
          </div>
        </div>
      )}

      {profileNotice && (
        <div className="fixed inset-0 z-[120] flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm">
          <div className="w-full max-w-sm rounded-3xl bg-white p-6 text-center shadow-2xl">
            <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-blue-50 text-blue-500">
              <Info className="h-6 w-6" />
            </div>
            <h3 className="text-lg font-black text-gray-900">{profileNotice.title}</h3>
            <p className="mt-3 text-sm font-semibold leading-relaxed text-gray-600">
              {profileNotice.message}
            </p>
            <button
              type="button"
              onClick={() => setProfileNotice(null)}
              className="mt-6 w-full rounded-xl bg-[#2563eb] py-3.5 font-bold text-white shadow-md transition-colors hover:bg-blue-700"
            >
              확인
            </button>
          </div>
        </div>
      )}

    </div>
  );
}
