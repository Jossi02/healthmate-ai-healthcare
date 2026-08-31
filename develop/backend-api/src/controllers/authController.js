const bcrypt = require('bcrypt');
const jwt = require('jsonwebtoken');
const supabase = require('../config/db');
const {
    bootstrapProfileRow,
    hasCompletedHealthProfile,
} = require('../services/profileService');
const {
    isValidBcryptHash,
    verifyPassword,
} = require('../utils/passwordHash');
const { getSecurityConfig } = require('../config/security');
const logger = require('../utils/logger');

const INVALID_CREDENTIALS_ERROR = '아이디 또는 비밀번호가 올바르지 않습니다.';

// @route   POST /api/v1/auth/signup
// @desc    회원가입
// @access  Public
exports.signup = async (req, res) => {
    try {
        const { login_id, password, nickname, email } = req.body;

        if (!login_id || !password || !nickname) {
            return res.status(400).json({ error: 'login_id, password, nickname은 필수 항목입니다.' });
        }

        // 1. 기존 가입 여부 확인 (login_id 중복 체크)
        const { data: existingUser } = await supabase
            .from('users')
            .select('login_id')
            .eq('login_id', login_id)
            .single();

        if (existingUser) {
            return res.status(409).json({ error: '이미 사용 중인 아이디입니다.' });
        }
        
        // 이메일 중복 체크 (이메일이 제공된 경우)
        if (email) {
            const { data: existingEmail } = await supabase
                .from('users')
                .select('email')
                .eq('email', email)
                .single();
            if (existingEmail) {
                return res.status(409).json({ error: '이미 사용 중인 이메일입니다.' });
            }
        }

        // 2. 비밀번호 암호화
        const saltRounds = 10;
        const password_hash = await bcrypt.hash(password, saltRounds);

        // 3. DB에 유저 생성
        const { data: newUser, error } = await supabase
            .from('users')
            .insert({
                login_id,
                password_hash,
                nickname,
                email: email || null
            })
            .select('user_id, login_id, nickname, email, created_at')
            .single();

        if (error) throw error;

        try {
            await bootstrapProfileRow(supabase, newUser.user_id);
        } catch (profileError) {
            logger.warn('Signup profile bootstrap warning.', profileError);
        }

        res.status(201).json({
            message: '회원가입이 성공적으로 완료되었습니다.',
            user: newUser
        });

    } catch (err) {
        logger.error('Signup error.', err);
        res.status(500).json({ error: '회원가입 중 서버 에러가 발생했습니다.' });
    }
};

// @route   POST /api/v1/auth/login
// @desc    로그인 (JWT 발급)
// @access  Public
exports.login = async (req, res) => {
    try {
        const { login_id, password } = req.body;

        if (!login_id || !password) {
            return res.status(400).json({ error: '아이디와 비밀번호를 입력해주세요.' });
        }

        // 1. 유저 조회
        const { data: user, error } = await supabase
            .from('users')
            .select('user_id, login_id, password_hash, nickname, email')
            .eq('login_id', login_id)
            .single();

        if (error || !user) {
            return res.status(401).json({ error: INVALID_CREDENTIALS_ERROR });
        }

        // 2. 비밀번호 검증
        const isMatch = await verifyPassword(password, user.password_hash);
        if (!isMatch) {
            if (!isValidBcryptHash(user.password_hash)) {
                logger.warn('Invalid password hash format for login.', { login_id: user.login_id });
            }
            return res.status(401).json({ error: INVALID_CREDENTIALS_ERROR });
        }

        // 3. 온보딩(건강 프로필) 작성 여부 확인
        // Profile status should not break authentication for legacy rows.
        let hasHealthProfile = false;
        try {
            const { data: profile, error: profileError } = await supabase
                .from('user_health_profiles')
                .select('user_id, gender, age, height, weight, goal, activity_level, mbti, allergies, medical_history')
                .eq('user_id', user.user_id)
                .maybeSingle();

            if (profileError) {
                logger.warn('Login profile lookup warning.', profileError);
            } else {
                hasHealthProfile = hasCompletedHealthProfile(profile || {});
            }
        } catch (profileError) {
            logger.warn('Login profile status warning.', profileError);
        }

        // 4. JWT 토큰 생성
        const payload = {
            user_id: String(user.user_id),
            login_id: String(user.login_id)
        };

        const security = getSecurityConfig();
        const token = jwt.sign(payload, security.jwtSecret, {
            algorithm: 'HS256',
            expiresIn: security.jwtExpiresIn,
        });

        const responseUser = {
            user_id: user.user_id,
            login_id: user.login_id,
            nickname: user.nickname,
            email: user.email,
            has_health_profile: hasHealthProfile,
        };

        res.json({
            message: '로그인에 성공했습니다.',
            token,
            user: responseUser
        });

    } catch (err) {
        logger.error('Login error.', err);
        res.status(500).json({ error: '로그인 중 서버 에러가 발생했습니다.' });
    }
};

// @route   GET /api/v1/auth/me
// @desc    현재 로그인된 유저 정보 조회 (토큰 검증용)
// @access  Private
exports.getMe = async (req, res) => {
    try {
        const userId = req.user.user_id;

        const { data: user, error } = await supabase
            .from('users')
            .select('user_id, login_id, nickname, email, created_at')
            .eq('user_id', userId)
            .single();
            
        if (error || !user) {
            return res.status(404).json({ error: '사용자를 찾을 수 없습니다.' });
        }

        // 온보딩(건강 프로필) 작성 여부 확인
        const { data: profile } = await supabase
            .from('user_health_profiles')
            .select('user_id, gender, age, height, weight, goal, activity_level, mbti, allergies, medical_history')
            .eq('user_id', userId)
            .maybeSingle();

        user.has_health_profile = hasCompletedHealthProfile(profile);

        res.json(user);
    } catch (err) {
        res.status(500).json({ error: '유저 정보 조회 중 서버 에러가 발생했습니다.' });
    }
};
