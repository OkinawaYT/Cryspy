#!/bin/bash
#source ~/.py39/bin/activate

cd "${PWD}"
echo "${PWD}"

# --- 設定をinput.datから読み込む ---
# ex)
# tot_max=8
# min=(1 1 0)
# max=(7 7 2)
# atype=("Pd" "Cu" "Co")
#---------------------------------
source input.dat

# natot になる nat の組み合わせを生成する関数
generate_nat_combinations() {
    local natot=$1
    local num_atype=$2
    local min=("${@:3:$num_atype}")  # min 配列
    local max=("${@:$((3 + num_atype)):$num_atype}")  # max 配列
    local i

    # ベースケース: 最後の元素（再帰的に解決）
    if ((num_atype == 1)); then
        for ((i=${min[0]}; i<=${max[0]}; i++)); do
            if ((i <= natot)); then
                echo "$i"
            fi
        done
        return
    fi

    # 再帰的に組み合わせを生成
    for ((i=${min[0]}; i<=${max[0]}; i++)); do
        local remaining_natot=$((natot - i))
        generate_nat_combinations "$remaining_natot" $((num_atype - 1)) "${min[@]:1}" "${max[@]:1}" | while read sub_nat; do
            echo "$i $sub_nat"
        done
    done
}

# cryspy を実行する関数
run_cryspy() {
    cryspy & pid=$! # cryspyをバックグラウンド実行し、プロセスIDを変数pidに格納

    # cryspyコマンドの終了をポーリング
    while ps -p $pid > /dev/null; do
        sleep 1 # 1秒ごとに確認
    done

    if [ $? -eq 0 ]; then
        python makeCIF.py
        sleep 5
        [ -e cryspy.stat ] && rm -r cryspy.stat
        [ -e err_cryspy ] && rm -r err_cryspy
        [ -e lock_cryspy ] && rm -r lock_cryspy
        [ -e log_cryspy ] && rm -r log_cryspy
        [ -e data ] && rm -r data
    else
        echo "cryspy command failed."
    fi
}

# --- 全ての組み合わせを生成 ---
combinations=()
for ((natot=1; natot<=tot_max; natot++)); do
    while IFS= read -r nat; do
        total_sum=$(echo "$nat" | tr ' ' '\n' | awk '{s+=$1} END {print s}')
        if ((total_sum == natot)); then
            echo "natot: ${natot}, nat: ${nat}, total_sum: ${total_sum}"
            combinations+=("${natot} ${nat}")
        fi
    done < <(generate_nat_combinations "${natot}" "${#atype[@]}" "${min[@]}" "${max[@]}")
done

echo "combinations: ${combinations[@]}"
echo "Number of combinations: ${#combinations[@]}"
echo "========================================="

# --- メイン処理 ---
for combination in "${combinations[@]}"; do
    natot=$(echo "$combination" | awk '{print $1}')
    nat=$(echo "$combination" | awk '{$1=""; print $0}' | xargs)

    echo "========================================="
    echo "natot: ${natot}"
    echo "nat: ${nat}"

    # 0 を含む場合の処理
    nat_array=($nat)
    new_atype=()
    new_nat=()
    temp_mindist=()
    new_mindist=()
    for ((i=0; i<${#nat_array[@]}; i++)); do
        if ((nat_array[i] != 0)); then
            label=$((i + 1))
            new_atype+=("${atype[i]}")
            new_nat+=("${nat_array[i]}")
            mindist_var="mindist_$((label))"
            eval "temp=(\"\${${mindist_var}[@]}\")"
            temp_mindist+=("${temp[@]}")
        fi
    done

    # 新しい mindist を構築
    for ((i=0; i<${#new_atype[@]}; i++)); do
        new_mindist_row=()
        for ((j=0; j<${#new_atype[@]}; j++)); do
            original_index=$(printf "%s\n" "${atype[@]}" | grep -n "${new_atype[j]}" | cut -d: -f1)
            original_index=$((original_index - 1 + ${#atype[@]} * i))
            new_mindist_row+=("${temp_mindist[original_index]}")
        done
        new_mindist+=("${new_mindist_row[@]}")
    done

    # 新しい atype と nat が空の場合はスキップ
    if (( ${#new_atype[@]} == 0 )); then
        echo "Skip (new_atype is empty)."
        continue
    fi

    # cryspy.in を編集
    sed -i "" "s/natot =.*/natot = ${natot}/" cryspy.in
    sed -i "" "s/atype =.*/atype = ${new_atype[*]}/" cryspy.in
    sed -i "" "s/nat =.*/nat = ${new_nat[*]}/" cryspy.in

    # mindist を設定
    for ((i=0; i<${#new_atype[@]}; i++)); do
        new_mindist_values=()
        for ((j=0; j<${#new_atype[@]}; j++)); do
            new_mindist_values+=("${new_mindist[i*${#new_atype[@]}+j]}")
        done
        echo "mindist_$((i+1)) = ${new_mindist_values[*]}"
        sed -i "" "s/mindist_$((i+1)) =.*/mindist_$((i+1)) = ${new_mindist_values[*]}/" cryspy.in
    done

    run_cryspy
done